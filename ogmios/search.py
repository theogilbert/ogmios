"""Hybrid search: BM25 (FTS5) and embedding similarity, merged with reciprocal rank fusion,
then grouped by section so that each day appears once."""

import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .embed import Embedder
from .parse import blank
from .when import Period

CANDIDATES = 200
RRF_K = 60
# Embeddings are better at paraphrases and translations; keywords still rescue jargon
# (ticket ids, hostnames) that the model never saw.
KEYWORD_WEIGHT = 0.5

# fmt: off
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could",
    "did", "do", "does", "for", "from", "had", "has", "have", "how", "i", "in", "is",
    "it", "its", "me", "my", "of", "on", "or", "our", "so", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "to", "us", "was", "we", "were",
    "what", "when", "where", "which", "who", "why", "will", "with", "would", "you",
    "your", "about", "into", "any", "au", "aux", "avec", "ce", "ces", "cette", "dans",
    "de", "des", "du", "elle", "en", "est", "et", "il", "ils", "je", "la", "le", "les",
    "leur", "lui", "ma", "mais", "mes", "mon", "ne", "nos", "notre", "nous", "ou", "où",
    "par", "pas", "pour", "qu", "que", "qui", "sa", "se", "ses", "son", "sur", "ta",
    "te", "tes", "ton", "tu", "un", "une", "vos", "votre", "vous", "y", "quand", "quel",
    "quelle", "quels", "quelles", "comment", "pourquoi", "été", "être", "avoir", "fait",
    "faire", "ai", "avons", "avez", "ont",
}
# Words that ask what happened rather than about something: "what did I do yesterday".
FILLER = {
    "do", "doing", "done", "work", "worked", "working", "happen", "happened",
    "write", "wrote", "written", "note", "up", "get", "got", "fais", "faisais",
    "passe", "quoi", "travaille", "ecrit", "bosse",
}
# fmt: on

_WORD_RE = re.compile(r"\w+")
# Words glued by punctuation are jargon (NET-881, srv-sftp-part01): match them as a phrase.
_COMPOUND_RE = re.compile(r"\w+(?:[-_./:]\w+)+")


@dataclass
class Hit:
    path: str
    line: int  # 1-based
    title: str
    date: str | None
    snippet: str
    score: float
    section: int = 0  # its id in the index
    # The section's chunks that scored, best first: (kind, start, end), lines 0-based.
    chunks: list[tuple[str, int, int]] = field(default_factory=list)


def query_terms(query: str) -> list[str]:
    """FTS5 terms: single words (prefix-matched) and compounds (as "a b" phrases)."""
    terms = []
    query = query.lower()
    for compound in _COMPOUND_RE.findall(query):
        terms.append(" ".join(_WORD_RE.findall(compound)))
    for word in _WORD_RE.findall(_COMPOUND_RE.sub(" ", query)):
        if word in STOPWORDS or len(word) < 2:
            continue
        # Crude plural folding; the trailing '*' makes it a prefix match ("flow" → "flows").
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        terms.append(word)
    return list(dict.fromkeys(terms))


def is_about_anything(query: str) -> bool:
    """Whether a query says nothing of a topic ("what did I do", "qu'ai-je fait")."""
    # Word by word: "qu'ai-je" is not jargon.
    words = [w for w in _WORD_RE.findall(query.lower()) if len(w) > 1]
    return all(
        w in STOPWORDS or _fold(w) in FILLER or _fold(w).removesuffix("s") in FILLER
        for w in words
    )


def _period_filter(period: Period | None) -> tuple[str, list[str]]:
    """SQL condition on sections `s`, and its parameters."""
    if period is None:
        return "1", []
    return "s.date BETWEEN ? AND ?", [period.start.isoformat(), period.end.isoformat()]


def _fts_ranking(
    db: sqlite3.Connection, terms: list[str], period: Period | None
) -> list[int]:
    if not terms:
        return []
    expression = " OR ".join(f'"{t}"' if " " in t else f'"{t}"*' for t in terms)
    condition, params = _period_filter(period)
    rows = db.execute(
        "SELECT f.rowid FROM chunks_fts f"
        " JOIN chunks c ON c.id = f.rowid JOIN sections s ON s.id = c.section_id"
        f" WHERE chunks_fts MATCH ? AND {condition} ORDER BY bm25(chunks_fts) LIMIT ?",
        (expression, *params, CANDIDATES),
    ).fetchall()
    return [r[0] for r in rows]


def _vector_ranking(
    db: sqlite3.Connection, embedder: Embedder, q: np.ndarray, period: Period | None
) -> list[int]:
    condition, params = _period_filter(period)
    rows = db.execute(
        "SELECT c.id, e.vector FROM chunks c"
        " JOIN embeddings e ON e.hash = c.hash AND e.model = ?"
        f" JOIN sections s ON s.id = c.section_id WHERE {condition}",
        (embedder.name, *params),
    ).fetchall()
    if not rows:
        return []
    ids = np.array([r[0] for r in rows])
    matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(
        len(rows), -1
    )
    scores = matrix @ (q / np.linalg.norm(q)) / np.linalg.norm(matrix, axis=1)
    top = np.argsort(-scores)[:CANDIDATES]
    return [int(i) for i in ids[top]]


def search(
    db: sqlite3.Connection,
    embedder: Embedder | None,
    query: str,
    limit: int = 20,
    period: Period | None = None,
) -> list[Hit]:
    terms = query_terms(query)
    rankings = [
        (
            KEYWORD_WEIGHT if embedder is not None else 1.0,
            _fts_ranking(db, terms, period),
        )
    ]
    q = embedder.query(query) if embedder is not None else None
    if embedder is not None and q is not None:
        rankings.append((1.0, _vector_ranking(db, embedder, q, period)))

    chunk_scores: dict[int, float] = {}
    for weight, ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            chunk_scores[chunk_id] = chunk_scores.get(chunk_id, 0.0) + weight / (
                RRF_K + rank + 1
            )
    if not chunk_scores:
        return []

    placeholders = ",".join("?" * len(chunk_scores))
    rows = db.execute(
        "SELECT c.id, c.start_line, c.end_line, s.id, s.path, s.title, s.date,"
        " s.start_line, c.kind"
        f" FROM chunks c JOIN sections s ON s.id = c.section_id WHERE c.id IN ({placeholders})",
        list(chunk_scores),
    ).fetchall()

    # One hit per section, positioned on its best chunk.
    best: dict[int, tuple[float, tuple]] = {}
    scored: dict[int, list[tuple[float, tuple[str, int, int]]]] = {}
    for row in rows:
        score = chunk_scores[row[0]]
        if row[3] not in best or score > best[row[3]][0]:
            best[row[3]] = (score, row)
        scored.setdefault(row[3], []).append((score, (row[8], row[1], row[2])))

    ranked = sorted(best.values(), key=lambda item: -item[0])[:limit]
    file_lines: dict[str, list[str]] = {}
    hits = []
    unmatched: list[tuple[int, list[int]]] = []  # hit, and its lines to choose from
    for score, (_, start, end, section, path, title, date, header, _) in ranked:
        if path not in file_lines:
            file_lines[path] = (
                Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            )
        lines = file_lines[path]
        content = [i for i in range(start, min(end, len(lines))) if not blank(lines[i])]
        # Not the header, nor a bare label ("> misc"), when the chunk holds more.
        content = (
            [i for i in content if i != header and len(lines[i].split()) > 2]
            or [i for i in content if i != header]
            or content
            or [start]
        )
        line, matched = _best_line(lines, content, terms)
        if not matched:
            unmatched.append((len(hits), content))
        snippet = lines[line].strip() if line < len(lines) else ""
        chunks = [c for _, c in sorted(scored[section], key=lambda item: -item[0])]
        hits.append(Hit(path, line + 1, title, date, snippet, score, section, chunks))

    if embedder is not None and q is not None and unmatched:
        # No query word on any line: point at the line closest in meaning.
        texts = [
            file_lines[hits[h].path][i].strip()
            for h, content in unmatched
            for i in content
        ]
        scores = embedder.passages(texts) @ q
        offset = 0
        for h, content in unmatched:
            closest = content[int(np.argmax(scores[offset : offset + len(content)]))]
            offset += len(content)
            snippet = file_lines[hits[h].path][closest].strip()
            hits[h] = replace(hits[h], line=closest + 1, snippet=snippet)

    return hits


def _fold(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(c)
    )


def _best_line(
    lines: list[str], content: list[int], terms: list[str]
) -> tuple[int, bool]:
    """The line matching the most query terms, else the first; and whether any matched."""
    folded_terms = [_fold(t).replace(" ", "") for t in terms]
    best, best_count = content[0], 0
    for i in content:
        folded = _fold(lines[i])
        # Also without punctuation, so that the compound "net 881" finds "NET-881".
        haystack = folded + " " + re.sub(r"\W+", "", folded)
        count = sum(t in haystack for t in folded_terms)
        if count > best_count:
            best, best_count = i, count
    return best, best_count > 0
