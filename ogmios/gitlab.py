"""The issues of a GitLab project, mirrored as markdown for the index: one file per issue,
its description and comments under the day they were written.

    # group/project#5 Export fails on empty files
    url: https://gitlab.example.com/group/project/-/issues/5
    state: closed · author: alice · assignees: bob · labels: bug

    ## 2024-03-14 · group/project#5 Export fails on empty files

    - alice opened the issue:

        Steps: ...

    - bob:

        Reproduced, the parser chokes on ...

The bodies are indented under their author, so that each is a block of the day, and so
that their own headings and dates are not taken for the days of the issue. Of the
system notes ("added ~bug label"), only closing and reopening are kept.

Only `ogmios fetch` talks to GitLab; the index reads the files it wrote.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from pathlib import Path

from .parse import SplitOptions

DELIMITER = re.compile(r"^## \d{4}-\d{2}-\d{2}\b")
SPLIT = SplitOptions(split="dates", delimiters=[DELIMITER])
STATE_FILE = ".state.json"  # not markdown: not indexed
PER_PAGE = 100
TIMEOUT = 30


def project_url(value: str) -> str:
    """The project's URL, normalized: "https://host/group/project"."""
    parsed = urllib.parse.urlsplit(value.strip())
    path = parsed.path.split("/-/")[0].strip("/").removesuffix(".git")
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "/" not in path:
        raise ValueError(
            f"not a GitLab project URL: {value!r}, expected https://HOST/GROUP/PROJECT"
        )
    return f"{parsed.scheme}://{parsed.netloc}/{path}"


def project_path(url: str) -> str:
    return urllib.parse.urlsplit(url).path.strip("/")


def host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc


def default_name(url: str) -> str:
    return project_path(url).rsplit("/", 1)[-1]


def mirror_dir(root: Path, url: str) -> Path:
    return root / host(url).replace(":", "_") / project_path(url)


def issue_url(path: Path) -> str | None:
    """The web URL of a mirrored issue, from its file."""
    try:
        with path.open(encoding="utf-8") as f:
            for _, line in zip(range(3), f):
                if line.startswith("url: "):
                    return line[5:].strip()
    except OSError:
        pass
    return None


@dataclass
class FetchStats:
    written: int
    removed: int


def fetch(
    url: str,
    directory: Path,
    token: str | None,
    full: bool = False,
    progress: Callable[[int], None] = lambda n: None,
) -> FetchStats:
    """Mirror the project's issues into `directory`: those updated since the last fetch,
    or all of them with `full`, which also removes the files of issues gone since."""
    state_file = directory / STATE_FILE
    state = _read_state(state_file)
    if state.get("url") != url:
        full = True
    api = _Api(url, token)
    params = {
        "scope": "all",
        "state": "all",
        "order_by": "updated_at",
        "sort": "asc",
    }
    if not full and state.get("updated_after"):
        params["updated_after"] = state["updated_after"]
    directory.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    for issue in api.pages("issues", params):
        notes = list(
            api.pages(
                f"issues/{issue['iid']}/notes",
                {"sort": "asc", "order_by": "created_at"},
            )
        )
        name = f"{issue['iid']}.md"
        _write(directory / name, render(project_path(url), issue, notes))
        seen.add(name)
        # Oldest change first: an interrupted fetch resumes where it stopped.
        state = {"url": url, "updated_after": issue["updated_at"]}
        _write(state_file, json.dumps(state) + "\n")
        progress(len(seen))

    removed = 0
    if full:
        for f in directory.glob("*.md"):
            if f.name not in seen:
                f.unlink()
                removed += 1
        _write(state_file, json.dumps({**state, "url": url}) + "\n")
    return FetchStats(len(seen), removed)


def render(project: str, issue: dict, notes: list[dict]) -> str:
    ref = f"{project}#{issue['iid']}"
    heading = f"{ref} {_one_line(issue['title'])}"
    meta = [f"state: {issue['state']}", f"author: {_user(issue.get('author'))}"]
    if issue.get("assignees"):
        meta.append(f"assignees: {', '.join(_user(a) for a in issue['assignees'])}")
    if issue.get("labels"):
        meta.append(f"labels: {', '.join(_label(l) for l in issue['labels'])}")
    if issue.get("milestone"):
        meta.append(f"milestone: {issue['milestone']['title']}")
    lines = [f"# {heading}", f"url: {issue['web_url']}", " · ".join(meta)]

    # (when, who, what they did, what they wrote)
    events = [
        (
            _local(issue["created_at"]),
            _user(issue.get("author")),
            " opened the issue",
            issue.get("description") or "",
        )
    ]
    for note in notes:
        who = _user(note.get("author"))
        body = note.get("body") or ""
        if not note.get("system"):
            events.append((_local(note["created_at"]), who, "", body))
        elif re.match(r"(closed|reopened)\b", body):
            events.append((_local(note["created_at"]), who, f" {body}", ""))

    events.sort(key=lambda e: e[0])  # stable: the description stays first
    for day, items in groupby(events, key=lambda e: e[0].date()):
        lines += ["", f"## {day.isoformat()} · {heading}"]
        for _, who, did, body in items:
            text = body.strip("\n")
            lines += ["", f"- {who}{did}{':' if text else ''}"]
            if text:
                lines.append("")
                lines += [
                    f"    {line}" if line.strip() else "" for line in text.split("\n")
                ]
    return "\n".join(lines) + "\n"


class _Api:
    def __init__(self, url: str, token: str | None):
        parsed = urllib.parse.urlsplit(url)
        project = urllib.parse.quote(project_path(url), safe="")
        self.base = f"{parsed.scheme}://{parsed.netloc}/api/v4/projects/{project}"
        self.url = url
        self.headers = {"PRIVATE-TOKEN": token} if token else {}

    def pages(self, endpoint: str, params: dict) -> Iterator[dict]:
        page: str | None = "1"
        while page:
            query = urllib.parse.urlencode(
                {**params, "per_page": PER_PAGE, "page": page}
            )
            items, page = self._get(f"{self.base}/{endpoint}?{query}")
            yield from items

    def _get(self, url: str) -> tuple[list[dict], str | None]:
        request = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.load(response), response.headers.get("X-Next-Page") or None
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ValueError(
                    f"{host(self.url)} rejected the token (401): check GITLAB_TOKEN"
                ) from None
            if e.code in (403, 404):
                raise ValueError(
                    f"{self.url}: no such project, or the token cannot read its issues"
                    f" ({e.code})"
                ) from None
            raise ValueError(
                f"{self.url}: GitLab answered {e.code} {e.reason}"
            ) from None
        except urllib.error.URLError as e:
            raise ValueError(f"cannot reach {host(self.url)}: {e.reason}") from None


def _read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, text: str) -> None:
    partial = path.with_name(path.name + ".part")
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)


def _local(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp).astimezone()


def _user(user: dict | str | None) -> str:
    if isinstance(user, dict):
        return user.get("username") or user.get("name") or "?"
    return user or "?"


def _label(label: dict | str) -> str:
    return label["name"] if isinstance(label, dict) else label


def _one_line(text: str) -> str:
    return " ".join(text.split())
