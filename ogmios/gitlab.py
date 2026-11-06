"""The issues or merge requests of a GitLab project, mirrored as markdown for the index:
one file for each, its description and comments under the day they were written.

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

A merge request also shows its source branch on each day, and keeps its threads together:

    # group/project!7 Fix the export
    url: https://gitlab.example.com/group/project/-/merge_requests/7
    state: merged · author: alice · reviewers: bob · branch: fix-export → main

    ## 2024-03-14 · group/project!7 Fix the export (fix-export)

    - alice opened the merge request:

        Handles empty files.

    - bob on src/parse.py:12:

        Should this skip them?

        - alice replied:

            Yes, done.

    ## 2024-03-16 · group/project!7 Fix the export (fix-export)

    - carol replied to bob on src/parse.py:12 ("Should this skip them?"):

        Same as NET-881.

    - bob approved this merge request

    - alice merged

A reply written on a later day says which thread it belongs to, since it is read alone.

Only `ogmios fetch` talks to GitLab; the index reads the files it wrote.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
from pathlib import Path

from .parse import SplitOptions

DELIMITER = re.compile(r"^## \d{4}-\d{2}-\d{2}\b")
SPLIT = SplitOptions(split="dates", delimiters=[DELIMITER])
STATE_FILE = ".state.json"  # not markdown: not indexed
PER_PAGE = 100
TIMEOUT = 30
QUOTE_CHARS = 80  # of the comment a later reply answers


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


def default_name(url: str, kind: "Kind") -> str:
    return project_path(url).rsplit("/", 1)[-1] + kind.name_suffix


def mirror_dir(root: Path, url: str) -> Path:
    return root / host(url).replace(":", "_") / project_path(url)


def web_url(path: Path) -> str | None:
    """The web URL of a mirrored issue or merge request, from its file."""
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
    kind: "Kind",
    full: bool = False,
    progress: Callable[[int], None] = lambda n: None,
) -> FetchStats:
    """Mirror the project's issues or merge requests into `directory`: those updated since
    the last fetch, or all of them with `full`, which also removes the files of those gone
    since."""
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
    for item in api.pages(kind.endpoint, params):
        comments = list(
            api.pages(f"{kind.endpoint}/{item['iid']}/{kind.comments}", kind.params)
        )
        name = f"{item['iid']}.md"
        _write(directory / name, kind.render(project_path(url), item, comments))
        seen.add(name)
        # Oldest change first: an interrupted fetch resumes where it stopped.
        state = {"url": url, "updated_after": item["updated_at"]}
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


@dataclass
class _Event:
    """What is written of something that happened on a day."""

    when: datetime
    header: str  # who, and what they did
    body: str = ""
    replies: list[tuple[str, str]] = field(default_factory=list)  # who, what they wrote


def render(project: str, issue: dict, notes: list[dict]) -> str:
    heading = f"{project}#{issue['iid']} {_one_line(issue['title'])}"
    events = [_opened(issue, "issue")]
    for note in notes:
        if not note.get("system"):
            events.append(
                _Event(
                    _local(note["created_at"]),
                    _user(note.get("author")),
                    note.get("body") or "",
                )
            )
        elif event := _system_event(note, r"(closed|reopened)\b"):
            events.append(event)
    return _document(heading, heading, issue, events)


def render_merge_request(project: str, mr: dict, discussions: list[dict]) -> str:
    heading = f"{project}!{mr['iid']} {_one_line(mr['title'])}"
    events = [_opened(mr, "merge request")]
    for discussion in discussions:
        events += _thread(discussion["notes"])
        for note in discussion["notes"]:
            if note.get("system") and (
                event := _system_event(note, r"(closed|reopened|approved)\b")
            ):
                events.append(event)
    if mr.get("merged_at"):
        who = _user(mr.get("merge_user"))
        events.append(_Event(_local(mr["merged_at"]), f"{who} merged"))
    branch = f" ({mr['source_branch']})" if mr.get("source_branch") else ""
    return _document(heading, heading + branch, mr, events)


def _opened(item: dict, noun: str) -> _Event:
    who = _user(item.get("author"))
    return _Event(
        _local(item["created_at"]),
        f"{who} opened the {noun}",
        item.get("description") or "",
    )


def _system_event(note: dict, keep: str) -> _Event | None:
    body = note.get("body") or ""
    if not re.match(keep, body):
        return None
    return _Event(_local(note["created_at"]), f"{_user(note.get('author'))} {body}")


def _thread(notes: list[dict]) -> list[_Event]:
    """A thread's comments, one event per day it went on: replies stay under the comment
    they follow, and one that opens a later day says what it answers."""
    said = sorted(
        (n for n in notes if not n.get("system")), key=lambda n: n["created_at"]
    )
    if not said:
        return []
    opener = said[0]
    place = _place(opener.get("position"))
    events = []
    for _, group in groupby(said, key=lambda n: _local(n["created_at"]).date()):
        first, *replies = group
        who = _user(first.get("author"))
        if first is opener:
            header = f"{who}{place}"
        else:
            answers = _user(opener.get("author"))
            header = f'{who} replied to {answers}{place} ("{_quote(opener)}")'
        events.append(
            _Event(
                _local(first["created_at"]),
                header,
                first.get("body") or "",
                [(_user(r.get("author")), r.get("body") or "") for r in replies],
            )
        )
    return events


def _place(position: dict | None) -> str:
    """Where in the changes a comment was left: " on src/parse.py:12"."""
    if not position:
        return ""
    path = position.get("new_path") or position.get("old_path")
    line = position.get("new_line") or position.get("old_line")
    if not path:
        return ""
    return f" on {path}:{line}" if line else f" on {path}"


def _quote(note: dict) -> str:
    text = _one_line(note.get("body") or "")
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + "…"


def _document(title: str, heading: str, item: dict, events: list[_Event]) -> str:
    """The file: `title` on top, `heading` on each day, the days in order."""
    lines = [f"# {title}", f"url: {item['web_url']}", " · ".join(_meta(item))]
    events = sorted(events, key=lambda e: e.when)  # stable: the description stays first
    for day, items in groupby(events, key=lambda e: e.when.date()):
        lines += ["", f"## {day.isoformat()} · {heading}"]
        for event in items:
            text = event.body.strip("\n")
            colon = ":" if text or event.replies else ""
            lines += ["", f"- {event.header}{colon}"]
            if text:
                lines += ["", *_indented(text, "    ")]
            for who, reply in event.replies:
                lines += ["", f"    - {who} replied:", ""]
                lines += _indented(reply.strip("\n"), "        ")
    return "\n".join(lines) + "\n"


def _indented(text: str, indent: str) -> list[str]:
    return [f"{indent}{line}" if line.strip() else "" for line in text.split("\n")]


def _meta(item: dict) -> list[str]:
    meta = [f"state: {item['state']}", f"author: {_user(item.get('author'))}"]
    if item.get("assignees"):
        meta.append(f"assignees: {', '.join(_user(a) for a in item['assignees'])}")
    if item.get("reviewers"):
        meta.append(f"reviewers: {', '.join(_user(r) for r in item['reviewers'])}")
    if item.get("labels"):
        meta.append(f"labels: {', '.join(_label(l) for l in item['labels'])}")
    if item.get("milestone"):
        meta.append(f"milestone: {item['milestone']['title']}")
    if item.get("source_branch"):
        meta.append(f"branch: {item['source_branch']} → {item['target_branch']}")
    return meta


@dataclass(frozen=True)
class Kind:
    """What a source mirrors of a project."""

    type: str  # of the source, in the config
    noun: str
    name_suffix: str  # a project can have a source of each kind
    endpoint: str
    comments: str  # the endpoint of what was said on one of them
    params: dict[str, str]
    render: Callable[[str, dict, list[dict]], str]


ISSUES = Kind(
    type="gitlab-issues",
    noun="issue",
    name_suffix="",
    endpoint="issues",
    comments="notes",
    params={"sort": "asc", "order_by": "created_at"},
    render=render,
)
MERGE_REQUESTS = Kind(
    type="gitlab-mrs",
    noun="merge request",
    name_suffix="-mrs",
    endpoint="merge_requests",
    comments="discussions",
    params={},
    render=render_merge_request,
)
KINDS = {kind.type: kind for kind in (ISSUES, MERGE_REQUESTS)}


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
            if e.code == 401 and not self.headers:
                # gitlab.com asks for one to read comments, even on a public project.
                raise ValueError(
                    f"{host(self.url)} asks for a token (401): set GITLAB_TOKEN"
                ) from None
            if e.code == 401:
                raise ValueError(
                    f"{host(self.url)} rejected the token (401): check GITLAB_TOKEN"
                ) from None
            if e.code in (403, 404):
                raise ValueError(
                    f"{self.url}: no such project, or the token cannot read this"
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
