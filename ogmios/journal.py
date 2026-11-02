"""A local log of every question asked: what was searched, what the model read and what
it answered, for troubleshooting a bad answer later; answers marked bad by `ogmios bad`;
and `ogmios report`, statistics from the log that hold nothing of the notes.

The log never leaves the machine and holds note excerpts: it is created readable by its
owner only. It is one JSON object a line, in two files, the current one and the one
before it, which together never exceed MAX_BYTES: when the current one would pass half
of it, it replaces the one before.
"""

import json
import os
import re
import statistics
import uuid
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from . import ask as asking
from .search import Hit
from .when import Period

MAX_BYTES = 100 * 1000 * 1000


def ask_record(
    *,
    question: str,
    today: date,
    period: Period | None,
    whole: bool,
    wordings: str,
    literal: bool,
    days: int,
    llm: str,
    model: str,
    hits: list[Hit],
    excerpts: list[asking.Excerpt],
    chat: list[dict],
    answer: str | None,
    prompt_tokens: int | None,
    answer_tokens: int,
    seconds: dict[str, float],
) -> dict:
    """What the log keeps of a question: enough to see again what the model was given,
    and to measure it without reading it."""
    read = {e.section for e in excerpts}
    ranks_read = [i for i, h in enumerate(hits, 1) if h.section in read]
    cited = asking.cited(answer, excerpts) if answer is not None else []
    return {
        "type": "ask",
        "id": uuid.uuid4().hex[:12],
        "at": datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "question": question,
        "words": len(re.findall(r"\w+", question)),
        "language": asking.language_of(question),
        "period": period
        and {
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
            "words": period.words,
            "days": (period.end - period.start).days + 1,
        },
        "whole": whole,
        "wordings": wordings,
        "literal": literal,
        "days": days,
        "llm": llm,
        "model": model,
        "budget": asking.BUDGET_CHARS,
        "hits": [
            {
                "section": h.section,
                "path": h.path,
                "line": h.line,
                "date": h.date,
                "score": round(h.score, 4),
                "chunks": [kind for kind, _, _ in h.chunks],
            }
            for h in hits
        ],
        "excerpts": [
            {
                "section": e.section,
                "path": e.path,
                "line": e.line,
                "date": e.date,
                "title": e.title,
                "text": e.text,
                "cited": e in cited,
            }
            for e in excerpts
        ],
        "cut": len(hits) - len(ranks_read),
        "worst_read": max(ranks_read, default=0),
        "prompt_chars": sum(len(m["content"]) for m in chat),
        "prompt_tokens": prompt_tokens,
        "answer": answer,
        "answer_tokens": answer_tokens,
        "cited": len(cited),
        "flagged": asking.unknown_dates(answer, excerpts) if answer else [],
        "seconds": {k: round(v, 2) for k, v in seconds.items()},
    }


def bad_record(ask: dict, day: str | None, note: str) -> dict:
    return {
        "type": "bad",
        "ask": ask["id"],
        "at": datetime.now().isoformat(timespec="seconds"),
        "day": day,
        "note": note,
    }


def previous(path: Path) -> Path:
    return path.with_name(f"{path.stem}.1{path.suffix}")


def append(path: Path, record: dict) -> None:
    data = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    half = MAX_BYTES // 2
    if len(data) > half:
        raise ValueError(f"log record of {len(data)} bytes, more than the log holds")
    path.parent.mkdir(parents=True, exist_ok=True)
    size = path.stat().st_size if path.exists() else 0
    if size + len(data) > half:
        path.replace(previous(path))
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "ab") as f:
        f.write(data)


def records(path: Path) -> list[dict]:
    """Every record logged, oldest first; a line cut short by a crash is skipped."""
    result = []
    for file in (previous(path), path):
        if not file.exists():
            continue
        for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                result.append(record)
    return result


def last_ask(path: Path) -> dict:
    asks = [r for r in records(path) if r.get("type") == "ask"]
    if not asks:
        raise ValueError(f"no question logged in {path}")
    return asks[-1]


def diagnose(ask: dict, day: str | None) -> tuple[str, int | None]:
    """Why an answer went wrong, given the day that holds the right one: the search did
    not find it, the budget left it out, or the model read it and got it wrong. With
    the day's rank in the search, if it was found."""
    if day is None:
        return "unknown", None
    if any(e["date"] == day for e in ask["excerpts"]):
        return "misread", _rank(ask, day)
    if ask["whole"]:
        period = ask["period"]
        if period["start"] <= day <= period["end"]:
            return "cut by the budget", None
        return "outside the period", None
    if (rank := _rank(ask, day)) is not None:
        return "cut by the budget", rank
    return "not found", None


def _rank(ask: dict, day: str) -> int | None:
    return next((i for i, h in enumerate(ask["hits"], 1) if h["date"] == day), None)


CAUSES = ("not found", "outside the period", "cut by the budget", "misread", "unknown")


def report(records: list[dict]) -> str:
    """Statistics of the questions logged, to share where the notes may not go: only
    counts, rates and measures, never a word of a question, a note, an answer or a path.
    Short enough to read in full before sharing it."""
    asks = [r for r in records if r.get("type") == "ask"]
    if not asks:
        return "no question logged"
    by_id = {a["id"]: a for a in asks}
    # The last mark of an answer counts, if it is still in the log.
    marks = {
        r["ask"]: r for r in records if r.get("type") == "bad" and r["ask"] in by_id
    }
    n = len(asks)
    searched = [a for a in asks if not a["whole"]]
    answered = [a for a in asks if a["answer"] is not None]
    timed = [a["seconds"] for a in asks]

    lines = [
        f"questions  {n}, {asks[0]['at'][:10]} to {asks[-1]['at'][:10]}",
        "  "
        + ", ".join(
            [
                *(f"{_pct(k, n)} {lang}" for lang, k in _counts(asks, "language")),
                f"{_pct(sum(a['period'] is not None for a in asks), n)} with a period",
                f"{_pct(n - len(searched), n)} reading a period whole",
                f"{_pct(sum(a['literal'] for a in asks), n)} --literal",
            ]
        ),
        f"  words   {_dist([a['words'] for a in asks])}",
        f"  period  days {_dist([a['period']['days'] for a in asks if a['period']])}",
    ]
    lines.append(f"answers  {len(answered)}")
    if answered:
        m = len(answered)
        uncited = _pct(sum(not a["cited"] for a in answered), m)
        flagged = _pct(sum(bool(a["flagged"]) for a in answered), m)
        lines += [
            f"  {uncited} cite nothing, {flagged} with a flagged date",
            f"  tokens  {_dist([a['answer_tokens'] for a in answered])}",
        ]
    if searched:
        cut = _pct(sum(a["cut"] > 0 for a in searched), len(searched))
        lines += [
            f"search  {len(searched)}",
            f"  days found     {_dist([len(a['hits']) for a in searched])}",
            f"  days read      {_dist([len(a['excerpts']) for a in searched])}",
            f"  days cut       {_dist([a['cut'] for a in searched])}  ({cut} of searches)",
            f"  last rank read {_dist([a['worst_read'] for a in searched if a['excerpts']])}",
        ]
    lines += [
        "prompt",
        f"  chars   {_dist([a['prompt_chars'] for a in asks])}",
        f"  tokens  {_dist([a['prompt_tokens'] for a in asks if a['prompt_tokens']])}",
        "seconds",
        *(
            f"  {step:9}{_dist([t[step] for t in timed if step in t], 1)}"
            for step in (
                "index",
                "load",
                "wordings",
                "search",
                "read",
                "write",
                "total",
            )
        ),
    ]
    budgets = Counter((a["budget"], a["llm"], a["model"]) for a in asks)
    for (budget, llm, model), k in budgets.most_common():
        lines.append(f"setup  {k}x budget {budget} chars, llm {llm}, model {model}")

    lines.append(f"marked bad  {len(marks)} ({_pct(len(marks), n)})")
    if marks:
        causes = Counter()
        ranks = []
        for mark in marks.values():
            cause, rank = diagnose(by_id[mark["ask"]], mark.get("day"))
            causes[cause] += 1
            if rank is not None:
                ranks.append((cause, rank))
        lines.append("  " + ", ".join(f"{c} {causes[c]}" for c in CAUSES if causes[c]))
        for cause in ("cut by the budget", "misread"):
            found = sorted(r for c, r in ranks if c == cause)
            if found:
                lines.append(f"  right day's rank, {cause}: {found}")
    return "\n".join(lines)


def _counts(asks: list[dict], key: str) -> list[tuple[str, int]]:
    return Counter(a[key] for a in asks).most_common()


def _pct(k: int, n: int) -> str:
    return f"{100 * k / n:.0f}%" if n else "-"


def _dist(values: list, digits: int = 0) -> str:
    """Median, 90th percentile and maximum."""
    if not values:
        return "-"
    values = sorted(values)
    p90 = values[min(len(values) - 1, int(0.9 * len(values)))]

    def f(x: float) -> str:
        return f"{x:.{digits}f}"

    return f"median {f(statistics.median(values))}, p90 {f(p90)}, max {f(values[-1])}"
