"""Answers to questions about the notes, written by a small local LLM from the days that
search finds, rather than from whole files: a 2B model reads a few thousand tokens in
seconds on a CPU, and makes fewer mistakes in a short context than in a long one.

The model, installed by `ogmios setup`, runs in-process with llama.cpp. Its file is
memory-mapped, so loading it takes about a second and nothing needs to stay running
between questions.
"""

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from . import hub
from .search import Hit
from .when import Period

if TYPE_CHECKING:
    import llama_cpp


@dataclass(frozen=True)
class LLMSpec:
    repo: str
    revision: str
    model_file: str


LLMS = {
    # Quantisation-aware trained by Google, so 4-bit loses little; Apache-2.0, not gated.
    "gemma-4-e2b": LLMSpec(
        "google/gemma-4-E2B-it-qat-q4_0-gguf",
        "675cff42a74c774d6cb76f76d8eacb49b48c9b93",
        "gemma-4-E2B_q4_0-it.gguf",
    ),
}
DEFAULT_LLM = "gemma-4-e2b"
CONTEXT_TOKENS = 4096
# About 2.5k tokens of notes (French takes ~2.5 characters a token), read at ~170 tokens/s
# on a laptop CPU: most of the time to the answer.
BUDGET_CHARS = 6_000
EXCERPT_CHARS = 2_500  # a long day is cut down to the lines around the hit
# What the best part of each day may take, at most; the rest goes to more of the best
# days, whose other parts may hold the answer when their best one only shares its words.
BREADTH_SHARE = 0.6

SYSTEM = """\
You answer questions about the user's own notes, using only the excerpts given.
Today is {today}. Each excerpt starts with its number in brackets and the day it was written; "…" marks lines left out.
If several excerpts answer the question, mention each of them, with its date.
End each sentence with the numbers of the excerpts it comes from, like [2] or [2, 5].
If the excerpts do not hold the answer, say so: never guess.
Answer briefly, in {language}."""


@dataclass
class Excerpt:
    path: str
    line: int  # 1-based, where the source points
    date: str | None
    title: str
    text: str


def excerpts_for(db: sqlite3.Connection, hits: list[Hit]) -> list[Excerpt]:
    """Of each day found, the part that matches best, for as many days as fit; then,
    with what budget is left, more of each day, a part at a time, round after round:
    its other parts that matched, then the items around them. Round by round, so that
    a day's second part, which may be the one that answers, comes before a third."""
    file_lines: dict[str, list[str]] = {}
    days = [day for hit in hits if (day := _Day.of(db, hit, file_lines))]
    breadth = int(BUDGET_CHARS * BREADTH_SHARE)
    chosen = []
    for day in days:
        if (size := len(day.text())) > breadth:
            break  # not a smaller, worse day instead
        chosen.append(day)
        breadth -= size
    left = BUDGET_CHARS - sum(len(day.text()) for day in chosen)
    pending = [(day, day.more()) for day in chosen]
    while pending:
        still = []
        for day, more in pending:
            # The next part of this day that fits, if any: it stays for another round.
            for lines in more:
                grown = len(day.text(lines)) - len(day.text())
                if 0 < grown <= left:
                    day.selected |= lines
                    left -= grown
                    still.append((day, more))
                    break
        pending = still
    excerpts = [day.excerpt() for day in chosen]
    # In the order they were written: "when" is easier to answer from a timeline.
    return sorted(excerpts, key=lambda e: (e.date or "", e.path, e.line))


class _Day:
    """A day found by the search, and the lines of it selected so far."""

    def __init__(self, hit: Hit, lines: list[str], header: int, end: int, items: list):
        self.hit, self.lines, self.header, self.end = hit, lines, header, end
        self.items = items  # (start, end) of its top-level items, if it has several
        focus = hit.line - 1
        finer = [(s, e) for kind, s, e in hit.chunks if kind != "section"]
        if finer:
            first = finer[0]
            # A pair of items: the one holding the focus first, the other later.
            within = [(s, e) for s, e in items if first[0] <= s and e <= first[1]]
            if len(within) > 1:
                first = next(((s, e) for s, e in within if s <= focus < e), within[0])
                finer.insert(1, (finer[0][0], finer[0][1]))
        else:
            # Only the day as a whole matched: the item holding the focus, else the day.
            first = next(
                ((s, e) for s, e in items if s <= focus < e), (header + 1, end)
            )
        self.selected = self._capped(*first, focus)
        self.matched = [set(range(s, e)) for s, e in finer[1:]]

    @classmethod
    def of(cls, db: sqlite3.Connection, hit: Hit, file_lines: dict) -> "_Day | None":
        row = db.execute(
            "SELECT start_line, end_line FROM sections WHERE id = ?", (hit.section,)
        ).fetchone()
        if row is None:
            return None
        items = db.execute(
            "SELECT start_line, end_line FROM chunks WHERE section_id = ?"
            " AND kind IN ('block', 'window') ORDER BY start_line",
            (hit.section,),
        ).fetchall()
        if hit.path not in file_lines:
            file_lines[hit.path] = (
                Path(hit.path)
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()
            )
        return cls(hit, file_lines[hit.path], row[0], row[1], items)

    def more(self) -> Iterator[set[int]]:
        """Lines worth adding, most useful first: other parts that matched, then the
        items nearest to what is selected."""
        yield from self.matched
        centre = sum(self.selected) / len(self.selected) if self.selected else 0
        for s, e in sorted(self.items, key=lambda item: abs(sum(item) / 2 - centre)):
            yield set(range(s, e))

    def text(self, extra: frozenset[int] | set[int] = frozenset()) -> str:
        """The selected lines, "…" marking the ones left out."""
        selected = self.selected | extra
        out: list[str] = []
        skipped = False
        for i in range(self.header + 1, min(self.end, len(self.lines))):
            line = self.lines[i]
            if not line.strip() or set(line.strip()) <= set("-*_="):
                continue
            if i in selected:
                if skipped:
                    out.append("…")
                out.append(line)
                skipped = False
            else:
                skipped = True
        if skipped and out:
            out.append("…")
        return "\n".join(out)

    def excerpt(self) -> Excerpt:
        hit = self.hit
        line = hit.line if hit.line - 1 in self.selected else min(self.selected) + 1
        return Excerpt(hit.path, line, hit.date, hit.title, self.text())

    def _capped(self, start: int, end: int, focus: int) -> set[int]:
        """The lines from `start` to `end`, cut around `focus` if too long."""
        lines = self.lines[start:end]
        first, last = _window(lines, focus - start, EXCERPT_CHARS)
        return set(range(start + first, start + last))


def excerpts_in(db: sqlite3.Connection, period: Period) -> list[Excerpt]:
    """The days of the period, taken most recent first until the budget is spent."""
    rows = db.execute(
        "SELECT path, start_line, end_line, title, date FROM sections"
        " WHERE date BETWEEN ? AND ? ORDER BY date DESC, path, start_line",
        (period.start.isoformat(), period.end.isoformat()),
    ).fetchall()
    return _excerpts([(*row, row[1]) for row in rows])


def _excerpts(sections: list[tuple]) -> list[Excerpt]:
    file_lines: dict[str, list[str]] = {}
    result: list[Excerpt] = []
    left = BUDGET_CHARS
    for path, start, end, title, day, focus in sections:
        if path not in file_lines:
            file_lines[path] = (
                Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            )
        # Not the header line: the date or title it holds labels the excerpt.
        lines = file_lines[path][start + 1 : end]
        first, last = _window(lines, focus - start - 1, min(EXCERPT_CHARS, left))
        text = "\n".join(lines[first:last]).strip()
        if not text:
            continue
        if len(text) > left:
            break
        result.append(Excerpt(path, focus + 1, day, title, text))
        left -= len(text)
    # In the order they were written: "when" is easier to answer from a timeline.
    return sorted(result, key=lambda e: (e.date or "", e.path, e.line))


def _window(lines: list[str], focus: int, budget: int) -> tuple[int, int]:
    """The lines around `focus` that fit in `budget` characters, as a slice."""
    focus = max(0, min(focus, len(lines) - 1))
    first, last = focus, focus + 1
    size = len(lines[focus]) + 1 if lines else 0
    while first > 0 or last < len(lines):
        # Mostly after the focus: a day's items are read top-down.
        grow_up = first > 0 and (
            last == len(lines) or focus - first < (last - focus) / 2
        )
        candidate = lines[first - 1] if grow_up else lines[last]
        if size + len(candidate) + 1 > budget:
            break
        size += len(candidate) + 1
        if grow_up:
            first -= 1
        else:
            last += 1
    return first, last


def messages(question: str, excerpts: list[Excerpt], today: date) -> list[dict]:
    language = language_of(question)
    notes = "\n\n".join(
        f"[{i}] {e.date or e.title}\n{e.text}" for i, e in enumerate(excerpts, 1)
    )
    return [
        {"role": "system", "content": SYSTEM.format(today=today, language=language)},
        # Named, and repeated last where a small model heeds it most: told "the language
        # of the question", it answers an English question about French notes in French.
        {
            "role": "user",
            "content": f"{notes}\n\nQuestion: {question}\n(Answer in {language}.)",
        },
    ]


# fmt: off
_FRENCH = {
    "le", "la", "les", "des", "du", "un", "une", "est", "et", "je", "j", "ai", "qu",
    "que", "qui", "quoi", "quand", "pourquoi", "comment", "quel", "quelle", "quels",
    "pour", "dans", "avec", "sur", "mon", "ma", "mes", "été", "fait", "au", "aux",
}
_ENGLISH = {
    "the", "a", "an", "is", "was", "were", "and", "i", "did", "do", "what", "when",
    "why", "how", "which", "who", "for", "in", "with", "on", "my", "to", "of", "have",
}
# fmt: on


def language_of(question: str) -> str:
    """English or French, the languages of the notes ogmios reads."""
    words = re.findall(r"\w+", question.lower())
    french = sum(w in _FRENCH for w in words) + bool(
        re.search(r"[éèêàùçâîô]", question)
    )
    english = sum(w in _ENGLISH for w in words)
    return "French" if french > english else "English"


def cited(answer: str, excerpts: list[Excerpt]) -> list[Excerpt]:
    """The excerpts an answer refers to, in the order of their numbers."""
    # "[2]", and also "[2, 5]"
    numbers = {
        int(n)
        for group in re.findall(r"\[([\d,\s]+)\]", answer)
        for n in group.split(",")
        if n.strip()
    }
    return [e for i, e in enumerate(excerpts, 1) if i in numbers]


def result(question: str, answer: str, excerpts: list[Excerpt]) -> dict:
    """What `ogmios ask --json` prints: every excerpt read, numbered as the answer cites
    them, and whether it does."""
    sources = cited(answer, excerpts)
    return {
        "question": question,
        "answer": answer,
        "excerpts": [
            {
                "n": i,
                "date": e.date,  # null for a section without one: see its title
                "title": e.title,
                "path": e.path,
                "line": e.line,
                "cited": e in sources,
            }
            for i, e in enumerate(excerpts, 1)
        ],
    }


def model_path(name: str, cache_dir: Path) -> Path:
    """Where `ogmios setup` installs the LLM."""
    spec = LLMS[name]
    return hub.model_dir(cache_dir, spec.repo, spec.revision) / spec.model_file


_quiet = None  # see `answer`


def load(model: Path) -> "llama_cpp.Llama":
    # Imported here: loading llama.cpp would slow down every other command.
    import ctypes

    import llama_cpp

    # Its warnings reach stderr even with verbose=False; errors still raise. Kept
    # referenced: llama.cpp calls it until the process exits, model teardown included.
    global _quiet
    _quiet = llama_cpp.llama_log_callback(lambda level, text, user_data: None)
    llama_cpp.llama_log_set(_quiet, ctypes.c_void_p(0))
    return llama_cpp.Llama(
        str(model),
        n_ctx=CONTEXT_TOKENS,
        swa_full=False,  # the sliding-window layers keep only their window
        verbose=False,
    )


def answer(llm: "llama_cpp.Llama", chat: list[dict]) -> Iterator[str]:
    """The answer, piece by piece as it is written; nothing comes until the model has
    read the notes, most of the wait."""
    stream = llm.create_chat_completion(
        chat,  # ty: ignore[invalid-argument-type]
        temperature=0,  # the same question, the same answer
        stream=True,
    )
    for chunk in stream:
        # Typed as a union with the non-streaming result.
        if text := chunk["choices"][0]["delta"].get("content"):  # ty: ignore
            yield text
