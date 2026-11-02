"""The log of questions, its size cap, the diagnosis of a bad answer, and the report,
which must not hold a word of the notes."""

import json
import re
from datetime import date
from typing import Any

import pytest

from ogmios import journal
from ogmios.ask import Excerpt
from ogmios.search import Hit
from ogmios.when import Period


def hit(section, day):
    return Hit(f"/notes/zorglub-{section}.md", 3, "Zorglub", day, "", 0.5, section)


def excerpt(section, day):
    return Excerpt(
        f"/notes/zorglub-{section}.md", 3, day, "Zorglub", "zorglub", section
    )


def record(**changes):
    fields: dict[str, Any] = {
        "question": "when did zorglub deploy the frobnicator?",
        "today": date(2026, 11, 2),
        "period": None,
        "whole": False,
        "wordings": "zorglub frobnicateur",
        "literal": False,
        "days": 20,
        "llm": "gemma-4-e2b",
        "model": "e5-small-int8",
        "hits": [hit(1, "2024-01-01"), hit(2, "2024-02-02"), hit(3, "2024-03-03")],
        "excerpts": [excerpt(1, "2024-01-01"), excerpt(2, "2024-02-02")],
        "chat": [{"role": "user", "content": "zorglub " * 10}],
        "answer": "Zorglub deployed it on 2024-01-01 [1].",
        "prompt_tokens": 40,
        "answer_tokens": 12,
        "seconds": {"search": 0.4, "read": 6.0, "total": 9.0},
    }
    return journal.ask_record(**(fields | changes))


def test_ask_record():
    r = record()
    assert r["cut"] == 1  # day 3 found, not read
    assert r["worst_read"] == 2
    assert r["cited"] == 1
    assert [e["cited"] for e in r["excerpts"]] == [True, False]
    assert r["flagged"] == []
    assert r["language"] == "English"
    json.dumps(r)


def test_records_survive_rotation_and_a_cut_line(tmp_path, monkeypatch):
    path = tmp_path / "asks.jsonl"
    for i in range(3):
        journal.append(path, {"type": "ask", "id": str(i)})
    with path.open("a") as f:
        f.write('{"type": "ask", "id"')  # a crash mid-write
    assert [r["id"] for r in journal.records(path)] == ["0", "1", "2"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_log_never_exceeds_its_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "MAX_BYTES", 1000)
    path = tmp_path / "asks.jsonl"
    for i in range(200):
        journal.append(path, {"type": "ask", "id": i, "pad": "x" * 30})
        total = sum(f.stat().st_size for f in tmp_path.iterdir())
        assert total <= 1000
    assert len(list(tmp_path.iterdir())) == 2
    ids = [r["id"] for r in journal.records(path)]
    assert ids == list(range(ids[0], 200))  # the most recent, in order


def test_record_larger_than_the_log_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "MAX_BYTES", 100)
    with pytest.raises(ValueError):
        journal.append(tmp_path / "asks.jsonl", {"pad": "x" * 100})


def test_last_ask(tmp_path):
    path = tmp_path / "asks.jsonl"
    with pytest.raises(ValueError):
        journal.last_ask(path)
    journal.append(path, {"type": "ask", "id": "a"})
    journal.append(path, {"type": "bad", "ask": "a"})
    assert journal.last_ask(path)["id"] == "a"


def test_diagnose():
    r = record()
    assert journal.diagnose(r, None) == ("unknown", None)
    assert journal.diagnose(r, "2024-02-02") == ("misread", 2)
    assert journal.diagnose(r, "2024-03-03") == ("cut by the budget", 3)
    assert journal.diagnose(r, "2025-05-05") == ("not found", None)


def test_diagnose_period_read_whole():
    period = Period(date(2024, 1, 1), date(2024, 1, 31), "january 2024")
    r = record(period=period, whole=True, hits=[], excerpts=[excerpt(1, "2024-01-30")])
    assert journal.diagnose(r, "2024-01-30") == ("misread", None)
    assert journal.diagnose(r, "2024-01-02") == ("cut by the budget", None)
    assert journal.diagnose(r, "2024-02-02") == ("outside the period", None)


def test_report_holds_nothing_of_the_notes():
    period = Period(date(2024, 1, 1), date(2024, 1, 31), "zorglub january")
    asks = [
        record(),
        record(
            question="quand zorglub a-t-il déployé ?", answer="Zorglub, 2024-09-09."
        ),
        record(period=period, whole=True, hits=[], answer="Zorglub [1]"),
    ]
    marks = [
        journal.bad_record(asks[0], "2024-03-03", "zorglub was wrong"),
        journal.bad_record(asks[1], None, "zorglub frobnicator"),
    ]
    out = journal.report(asks + marks)
    secret = set()
    for r in asks + marks:
        for value in (r.get("question"), r.get("answer"), r.get("note")):
            secret |= set(re.findall(r"[a-zé]{4,}", (value or "").lower()))
    secret |= {"zorglub", "frobnicator", "notes", "2024-01-01", "january"}
    assert not {w for w in secret if w in out.lower()}
    assert "cut by the budget 1, unknown 1" in out
    assert "questions  3" in out
    assert "67% English" in out and "33% French" in out
    assert "33% cite nothing, 33% with a flagged date" in out


def test_empty_report():
    assert journal.report([]) == "no question logged"
