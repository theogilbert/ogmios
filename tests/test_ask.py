"""What the LLM is given, and the sources printed after its answer; the model itself is not
run in unit tests."""

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from ogmios import ask, store
from ogmios.config import Source
from ogmios.search import search
from ogmios.when import Period

FIXTURE = Path(__file__).parent / "fixtures" / "done.md"


class FakeEmbedder:
    name = "fake"

    def passages(self, texts):
        return np.ones((len(list(texts)), 4), dtype=np.float32)


@pytest.fixture
def db(tmp_path):
    connection = store.connect(tmp_path / "index.sqlite")
    files = store.collect_files([Source(path=FIXTURE)])
    store.rebuild(connection, files, FakeEmbedder())  # ty: ignore[invalid-argument-type]
    return connection


def test_excerpts_are_whole_days_in_date_order(db):
    excerpts = ask.excerpts_for(db, search(db, None, "RESEAU-4512"))
    assert [e.date for e in excerpts] == ["2022-04-04", "2022-04-11", "2022-04-19"]
    first = excerpts[0]
    assert "## 2022-04-04" not in first.text  # the header is the label
    assert first.text.startswith("- Ouverture de flux")
    assert first.text.endswith("pas de retour")
    lines = FIXTURE.read_text().splitlines()
    assert "RESEAU-4512" in lines[first.line - 1]


def test_excerpts_in_period(db):
    period = Period(date(2022, 4, 1), date(2022, 4, 30), "april 2022")
    excerpts = ask.excerpts_in(db, period)
    assert [e.date for e in excerpts] == [
        "2022-04-04",
        "2022-04-11",
        "2022-04-19",
        "2022-04-21",
    ]


def test_budget_keeps_the_best(db, monkeypatch):
    monkeypatch.setattr(ask, "BUDGET_CHARS", 300)
    excerpts = ask.excerpts_for(db, search(db, None, "RESEAU-4512 validé"))
    assert sum(len(e.text) for e in excerpts) <= 300
    assert "2022-04-19" in [e.date for e in excerpts]


def test_long_day_is_cut_around_the_focus():
    lines = [f"line {i:02}" for i in range(100)]  # 8 characters each
    first, last = ask._window(lines, 50, 90)
    assert first <= 50 < last
    assert sum(len(l) + 1 for l in lines[first:last]) <= 90
    assert last - 50 > 50 - first  # mostly what follows


def test_cited_sources():
    excerpts = [ask.Excerpt("f.md", i, f"2024-01-0{i}", "", "x") for i in (1, 2, 3)]
    assert ask.cited("On the 3rd [3], then [1].", excerpts) == [
        excerpts[0],
        excerpts[2],
    ]
    assert ask.cited("No idea.", excerpts) == []


def test_messages_number_the_excerpts():
    excerpts = [ask.Excerpt("f.md", 1, "2024-01-01", "", "- did a thing")]
    system, user = ask.messages("what?", excerpts, date(2024, 1, 2))
    assert "2024-01-02" in system["content"]
    assert user["content"] == (
        "[1] 2024-01-01\n- did a thing\n\nQuestion: what?\n(Answer in English.)"
    )


@pytest.mark.parametrize(
    ("question", "language"),
    [
        ("when were the network flows to the sftp validated?", "English"),
        ("what happened with NET-881?", "English"),
        ("pourquoi le DAG a échoué en janvier ?", "French"),
        ("qu'ai-je fait hier", "French"),
        ("NET-881", "English"),
    ],
)
def test_language_of(question, language):
    assert ask.language_of(question) == language
