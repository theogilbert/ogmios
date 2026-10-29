"""Answers to questions about the notes, written by a small local LLM from the days that
search finds, rather than from whole files: a 2B model reads a few thousand tokens in
seconds on a CPU, and makes fewer mistakes in a short context than in a long one.

The model, installed by `ogmios setup`, runs in-process with llama.cpp. Its file is
memory-mapped, so loading it takes about a second and nothing needs to stay running
between questions.
"""

import importlib.util
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import hub
from .search import Hit
from .when import Period


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

# The tilde is outside the quotes, so that the shell expands it.
INSTALL = "uv tool install --editable ~/projects/ogmios'[ask]'"

SYSTEM = """\
You answer questions about the user's own notes, using only the excerpts given.
Today is {today}. Each excerpt starts with its number in brackets and the day it was written.
Cite the excerpts you use, like [2]. If they do not hold the answer, say so: never guess.
Answer briefly, in {language}."""


@dataclass
class Excerpt:
    path: str
    line: int  # 1-based, where the source points
    date: str | None
    title: str
    text: str


def excerpts_for(db: sqlite3.Connection, hits: list[Hit]) -> list[Excerpt]:
    """The day around each hit, taken best first until the budget is spent."""
    found = []
    for hit in hits:
        row = db.execute(
            "SELECT start_line, end_line FROM sections"
            " WHERE path = ? AND start_line < ? AND ? <= end_line",
            (hit.path, hit.line, hit.line),
        ).fetchone()
        if row is not None:
            found.append((hit.path, row[0], row[1], hit.title, hit.date, hit.line - 1))
    return _excerpts(found)


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


def model_path(name: str, cache_dir: Path) -> Path:
    """Where `ogmios setup` installs the LLM."""
    spec = LLMS[name]
    return hub.model_dir(cache_dir, spec.repo, spec.revision) / spec.model_file


_quiet = None  # see `answer`


def has_runtime() -> bool:
    """Whether llama-cpp-python, the optional `ask` dependency, is installed."""
    return importlib.util.find_spec("llama_cpp") is not None


def answer(model: Path, chat: list[dict]) -> Iterator[str]:
    """The answer, piece by piece as it is written."""
    import ctypes

    import llama_cpp

    # Its warnings reach stderr even with verbose=False; errors still raise. Kept
    # referenced: llama.cpp calls it until the process exits, model teardown included.
    global _quiet
    _quiet = llama_cpp.llama_log_callback(lambda level, text, user_data: None)
    llama_cpp.llama_log_set(_quiet, ctypes.c_void_p(0))
    llm = llama_cpp.Llama(
        str(model),
        n_ctx=CONTEXT_TOKENS,
        swa_full=False,  # the sliding-window layers keep only their window
        verbose=False,
    )
    stream = llm.create_chat_completion(
        chat,  # ty: ignore[invalid-argument-type]
        temperature=0.2,
        stream=True,
    )
    for chunk in stream:
        # Typed as a union with the non-streaming result.
        if text := chunk["choices"][0]["delta"].get("content"):  # ty: ignore
            yield text
