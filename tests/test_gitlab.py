"""Issues rendered as markdown and mirrored; GitLab itself is replaced by canned pages."""

import io
import json
import urllib.error
import urllib.parse
from email.message import Message
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
    stats = gitlab.fetch(URL, mirror, "secret", gitlab.ISSUES)
    assert (stats.written, stats.removed) == (3, 0)
    assert sorted(f.name for f in mirror.iterdir()) == [
        ".state.json",
        "1.md",
        "2.md",
        "3.md",
    ]
    assert gitlab.web_url(mirror / "2.md") == f"{URL}/-/issues/2"
    assert json.loads((mirror / ".state.json").read_text()) == {
        "url": URL,
        "updated_after": "2024-03-23T00:00:00Z",
    }

    # Only what changed since: the last one again (GitLab's bound is inclusive) and 4.
    fake.issues.append(issue(4, "2024-03-24T00:00:00Z"))
    fake.requests.clear()
    assert gitlab.fetch(URL, mirror, "secret", gitlab.ISSUES).written == 2
    assert fake.requests[0]["updated_after"] == "2024-03-23T00:00:00Z"

    # A full fetch drops the files of issues gone since.
    del fake.issues[0]
    stats = gitlab.fetch(URL, mirror, "secret", gitlab.ISSUES, full=True)
    assert (stats.written, stats.removed) == (3, 1)
    assert not (mirror / "1.md").exists()


def test_mirrored_issues_are_searched(tmp_path, fake):
    mirror = tmp_path / "mirror"
    gitlab.fetch(URL, mirror, "secret", gitlab.ISSUES)
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


MR = {
    "iid": 7,
    "title": "Fix the export",
    "state": "merged",
    "author": {"username": "alice"},
    "assignees": [],
    "reviewers": [{"username": "bob"}],
    "labels": [],
    "web_url": f"{URL}/-/merge_requests/7",
    "description": "Handles empty files.\n\n## Why\n\n2024-01-01 was fine.",
    "source_branch": "fix-export",
    "target_branch": "main",
    "created_at": "2024-03-14T09:00:00Z",
    "updated_at": "2024-03-16T12:00:00Z",
    "merged_at": "2024-03-16T12:00:00Z",
    "merge_user": {"username": "alice"},
}


def note(who: str, when: str, body: str, **fields) -> dict:
    return {
        "author": {"username": who},
        "body": body,
        "created_at": when,
        "system": False,
        **fields,
    }


POSITION = {"old_path": "src/parse.py", "new_path": "src/parse.py", "new_line": 12}
DISCUSSIONS = [
    {  # on the code, replied to that day and two days later
        "individual_note": False,
        "notes": [
            note(
                "bob",
                "2024-03-14T10:00:00Z",
                "Should this skip them?",
                type="DiffNote",
                position=POSITION,
            ),
            note("alice", "2024-03-14T11:00:00Z", "Yes, done."),
            note("carol", "2024-03-16T09:00:00Z", "NET-881 is the same."),
            note("alice", "2024-03-16T09:30:00Z", "Thanks."),
        ],
    },
    {  # a thread on the merge request itself
        "individual_note": False,
        "notes": [
            note("carol", "2024-03-15T09:00:00Z", "Needs a changelog?"),
            note("bob", "2024-03-15T09:10:00Z", "No."),
        ],
    },
    {  # a lone comment
        "individual_note": True,
        "notes": [note("bob", "2024-03-15T10:00:00Z", "Pipeline is green.")],
    },
    {
        "individual_note": True,
        "notes": [
            note(
                "bob",
                "2024-03-16T10:00:00Z",
                "approved this merge request",
                system=True,
            )
        ],
    },
    {
        "individual_note": True,
        "notes": [note("bob", "2024-03-16T10:01:00Z", "added 1 commit", system=True)],
    },
]


def test_render_merge_request(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    text = gitlab.render_merge_request("group/proj", MR, DISCUSSIONS)
    lines = text.splitlines()  # before the assert narrows `text` to a literal
    assert text == (
        "# group/proj!7 Fix the export\n"
        f"url: {URL}/-/merge_requests/7\n"
        "state: merged · author: alice · reviewers: bob · branch: fix-export → main\n"
        "\n"
        "## 2024-03-14 · group/proj!7 Fix the export (fix-export)\n"
        "\n"
        "- alice opened the merge request:\n"
        "\n"
        "    Handles empty files.\n"
        "\n"
        "    ## Why\n"
        "\n"
        "    2024-01-01 was fine.\n"
        "\n"
        "- bob on src/parse.py:12:\n"
        "\n"
        "    Should this skip them?\n"
        "\n"
        "    - alice replied:\n"
        "\n"
        "        Yes, done.\n"
        "\n"
        "## 2024-03-15 · group/proj!7 Fix the export (fix-export)\n"
        "\n"
        "- carol:\n"
        "\n"
        "    Needs a changelog?\n"
        "\n"
        "    - bob replied:\n"
        "\n"
        "        No.\n"
        "\n"
        "- bob:\n"
        "\n"
        "    Pipeline is green.\n"
        "\n"
        "## 2024-03-16 · group/proj!7 Fix the export (fix-export)\n"
        "\n"
        '- carol replied to bob on src/parse.py:12 ("Should this skip them?"):\n'
        "\n"
        "    NET-881 is the same.\n"
        "\n"
        "    - alice replied:\n"
        "\n"
        "        Thanks.\n"
        "\n"
        "- bob approved this merge request\n"
        "\n"
        "- alice merged\n"
    )
    sections = parse_file(Path("7.md"), lines, gitlab.SPLIT)
    assert [s.date for s in sections] == [
        None,
        "2024-03-14",
        "2024-03-15",
        "2024-03-16",
    ]
    # The branch is in each day's title, and a thread is one block of its day.
    assert all(s.title.endswith(" fix-export") for s in sections[1:])
    assert [b.title for b in sections[1].blocks] == [
        "alice opened the merge request",
        "bob on src/parse.py:12",
    ]


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (None, ""),
        ({"new_path": "a.py", "new_line": 3, "old_line": 2}, " on a.py:3"),
        ({"old_path": "a.py", "new_path": "a.py", "old_line": 2}, " on a.py:2"),
        ({"new_path": "logo.png", "position_type": "image"}, " on logo.png"),
    ],
)
def test_where_a_comment_was_left(position, expected):
    assert gitlab._place(position) == expected


def test_a_late_reply_quotes_the_start_of_what_it_answers(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    long = "word " * 40
    discussion = {
        "notes": [
            note("bob", "2024-03-14T10:00:00Z", long),
            note("alice", "2024-03-15T10:00:00Z", "Agreed."),
        ]
    }
    text = gitlab.render_merge_request(
        "group/proj", {**MR, "merged_at": None}, [discussion]
    )
    said = next(s for s in text.splitlines() if "replied to" in s)
    assert said.startswith('- alice replied to bob ("word word')
    assert said.endswith('…"):')
    assert len(said) < gitlab.QUOTE_CHARS + 40


def test_a_merge_request_with_little_to_say(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    mr = {**MR, "description": None, "merged_at": None, "state": "opened"}
    text = gitlab.render_merge_request(
        "group/proj",
        mr,
        [{"notes": [note("bob", "2024-03-14T10:00:00Z", "noted", system=True)]}],
    )
    assert text.endswith("- alice opened the merge request\n")


def test_fetch_merge_requests(tmp_path, monkeypatch):
    """Threads come from the discussions of each, over several pages."""
    requests = []

    def server(request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        params = dict(urllib.parse.parse_qsl(url.query))
        requests.append(url.path)
        if url.path.endswith("/merge_requests/7/discussions"):
            items = DISCUSSIONS
        else:
            assert url.path.endswith("/merge_requests")
            items = [MR]
        page = int(params["page"])
        return Response(
            items[2 * (page - 1) : 2 * page],
            {"X-Next-Page": str(page + 1) if len(items) > 2 * page else ""},
        )

    monkeypatch.setattr(gitlab.urllib.request, "urlopen", server)
    mirror = tmp_path / "mirror"
    assert gitlab.fetch(URL, mirror, "secret", gitlab.MERGE_REQUESTS).written == 1
    text = (mirror / "7.md").read_text()
    assert "NET-881 is the same." in text
    assert "approved this merge request" in text
    assert "added 1 commit" not in text
    assert gitlab.web_url(mirror / "7.md") == f"{URL}/-/merge_requests/7"
    assert not any("/issues" in path or "/notes" in path for path in requests)


@pytest.mark.parametrize(
    ("token", "expected"), [(None, "asks for a token"), ("bad", "rejected the token")]
)
def test_unauthorized(tmp_path, monkeypatch, token, expected):
    def server(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", Message(), None
        )

    monkeypatch.setattr(gitlab.urllib.request, "urlopen", server)
    with pytest.raises(ValueError, match=expected):
        gitlab.fetch(URL, tmp_path / "mirror", token, gitlab.ISSUES)


def test_mirrored_merge_requests_are_searched(tmp_path, monkeypatch):
    """A reply on a later day is found on that day, as are the branch and the title."""
    monkeypatch.setenv("TZ", "UTC")
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "7.md").write_text(
        gitlab.render_merge_request("group/proj", MR, DISCUSSIONS)
    )
    db = store.connect(tmp_path / "index.sqlite")

    class FakeEmbedder:
        name = "fake"

        def passages(self, texts):
            return np.ones((len(list(texts)), 4), dtype=np.float32)

    source = Source(path=mirror, glob="*.md", options=gitlab.SPLIT, name="proj-mrs")
    store.rebuild(db, store.collect_files([source]), FakeEmbedder())  # ty: ignore[invalid-argument-type]
    assert [h.date for h in search(db, None, "NET-881")] == ["2024-03-16"]
    assert [h.date for h in search(db, None, "changelog")] == ["2024-03-15"]
    days = ["2024-03-14", "2024-03-15", "2024-03-16"]
    assert days == sorted(h.date for h in search(db, None, "fix-export") if h.date)
    assert days == sorted(h.date for h in search(db, None, "export") if h.date)
