"""Issues rendered as markdown and mirrored; GitLab itself is replaced by canned pages."""

import io
import json
import urllib.parse
from pathlib import Path

import numpy as np
import pytest

from ogmios import gitlab, store
from ogmios.config import Source
from ogmios.parse import parse_file
from ogmios.search import search

URL = "https://gitlab.example.com/group/proj"


def issue(iid: int, updated: str, title: str = "Export fails", **fields) -> dict:
    return {
        "iid": iid,
        "title": title,
        "state": "opened",
        "author": {"username": "alice"},
        "assignees": [{"username": "bob"}],
        "labels": ["bug"],
        "web_url": f"{URL}/-/issues/{iid}",
        "description": "## Steps\n\nExport an empty file.\n\n2024-01-01 was fine.",
        "created_at": "2024-03-14T09:00:00Z",
        "updated_at": updated,
        **fields,
    }


NOTES = [
    {
        "author": {"username": "bob"},
        "body": "Reproduced: the parser chokes on NET-881.",
        "created_at": "2024-03-14T10:00:00Z",
        "system": False,
    },
    {
        "author": {"username": "bob"},
        "body": "added ~bug label",
        "created_at": "2024-03-14T10:01:00Z",
        "system": True,
    },
    {
        "author": {"username": "alice"},
        "body": "closed",
        "created_at": "2024-03-20T12:00:00Z",
        "system": True,
    },
]


def test_render(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    text = gitlab.render("group/proj", issue(5, "2024-03-20T12:00:00Z"), NOTES)
    lines = text.splitlines()  # before the assert narrows `text` to a literal
    assert text == (
        "# group/proj#5 Export fails\n"
        f"url: {URL}/-/issues/5\n"
        "state: opened · author: alice · assignees: bob · labels: bug\n"
        "\n"
        "## 2024-03-14 · group/proj#5 Export fails\n"
        "\n"
        "- alice opened the issue:\n"
        "\n"
        "    ## Steps\n"
        "\n"
        "    Export an empty file.\n"
        "\n"
        "    2024-01-01 was fine.\n"
        "\n"
        "- bob:\n"
        "\n"
        "    Reproduced: the parser chokes on NET-881.\n"
        "\n"
        "## 2024-03-20 · group/proj#5 Export fails\n"
        "\n"
        "- alice closed\n"
    )
    sections = parse_file(Path("5.md"), lines, gitlab.SPLIT)
    assert [(s.title, s.date) for s in sections] == [
        ("5", None),
        ("2024-03-14 group/proj#5 Export fails", "2024-03-14"),
        ("2024-03-20 group/proj#5 Export fails", "2024-03-20"),
    ]
    # The description and each comment are blocks of the day.
    assert [b.title for b in sections[1].blocks] == [
        "alice opened the issue",
        "bob",
    ]


class FakeGitLab:
    """Pages of issues and notes, as the API serves them, 2 per page."""

    def __init__(self, issues: list[dict]):
        self.issues = issues
        self.requests: list[dict] = []

    def __call__(self, request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        params = dict(urllib.parse.parse_qsl(url.query))
        self.requests.append({"path": url.path, **params})
        assert url.path.startswith("/api/v4/projects/group%2Fproj/")
        assert request.headers == {"Private-token": "secret"}
        if url.path.endswith("/notes"):
            items = NOTES
        else:
            after = params.get("updated_after", "")
            items = sorted(
                (i for i in self.issues if i["updated_at"] >= after),
                key=lambda i: i["updated_at"],
            )
        page = int(params["page"])
        return Response(
            items[2 * (page - 1) : 2 * page],
            {"X-Next-Page": str(page + 1) if len(items) > 2 * page else ""},
        )


class Response(io.BytesIO):
    def __init__(self, items: list[dict], headers: dict[str, str]):
        super().__init__(json.dumps(items).encode())
        self.headers = headers


@pytest.fixture
def fake(monkeypatch):
    server = FakeGitLab(
        [issue(i, f"2024-03-2{i}T00:00:00Z", f"issue {i}") for i in (1, 2, 3)]
    )
    monkeypatch.setattr(gitlab.urllib.request, "urlopen", server)
    return server


def test_fetch_is_incremental(tmp_path, fake):
    mirror = tmp_path / "mirror"
    stats = gitlab.fetch(URL, mirror, "secret")
    assert (stats.written, stats.removed) == (3, 0)
    assert sorted(f.name for f in mirror.iterdir()) == [
        ".state.json",
        "1.md",
        "2.md",
        "3.md",
    ]
    assert gitlab.issue_url(mirror / "2.md") == f"{URL}/-/issues/2"
    assert json.loads((mirror / ".state.json").read_text()) == {
        "url": URL,
        "updated_after": "2024-03-23T00:00:00Z",
    }

    # Only what changed since: the last one again (GitLab's bound is inclusive) and 4.
    fake.issues.append(issue(4, "2024-03-24T00:00:00Z"))
    fake.requests.clear()
    assert gitlab.fetch(URL, mirror, "secret").written == 2
    assert fake.requests[0]["updated_after"] == "2024-03-23T00:00:00Z"

    # A full fetch drops the files of issues gone since.
    del fake.issues[0]
    stats = gitlab.fetch(URL, mirror, "secret", full=True)
    assert (stats.written, stats.removed) == (3, 1)
    assert not (mirror / "1.md").exists()


def test_mirrored_issues_are_searched(tmp_path, fake):
    mirror = tmp_path / "mirror"
    gitlab.fetch(URL, mirror, "secret")
    db = store.connect(tmp_path / "index.sqlite")

    class FakeEmbedder:
        name = "fake"

        def passages(self, texts):
            return np.ones((len(list(texts)), 4), dtype=np.float32)

    source = Source(path=mirror, glob="*.md", options=gitlab.SPLIT, name="proj")
    store.rebuild(db, store.collect_files([source]), FakeEmbedder())  # ty: ignore[invalid-argument-type]
    hits = search(db, None, "NET-881")
    assert {h.title.split(" ", 1)[1] for h in hits} == {
        f"group/proj#{i} issue {i}" for i in (1, 2, 3)
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (URL, URL),
        (URL + "/", URL),
        (URL + ".git", URL),
        (URL + "/-/issues/5", URL),
        ("http://localhost:8080/a/b/c", "http://localhost:8080/a/b/c"),
    ],
)
def test_project_url(value, expected):
    assert gitlab.project_url(value) == expected
