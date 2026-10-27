"""Hybrid search: BM25 (FTS5) and embedding similarity, merged with reciprocal rank fusion,
then grouped by section so that each day appears once."""

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .embed import Embedder

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


def _fts_ranking(db: sqlite3.Connection, terms: list[str]) -> list[int]:
    if not terms:
        return []
    expression = " OR ".join(f'"{t}"' if " " in t else f'"{t}"*' for t in terms)
    rows = db.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
        (expression, CANDIDATES),
    ).fetchall()
    return [r[0] for r in rows]


def _vector_ranking(
    db: sqlite3.Connection, embedder: Embedder, query: str
) -> list[int]:
    rows = db.execute(
        "SELECT c.id, e.vector FROM chunks c JOIN embeddings e ON e.hash = c.hash AND e.model = ?",
        (embedder.name,),
    ).fetchall()
    if not rows:
        return []
    ids = np.array([r[0] for r in rows])
    matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(
        len(rows), -1
    )
    q = embedder.query(query)
    scores = matrix @ (q / np.linalg.norm(q)) / np.linalg.norm(matrix, axis=1)
    top = np.argsort(-scores)[:CANDIDATES]
    return [int(i) for i in ids[top]]


def search(
    db: sqlite3.Connection,
    embedder: Embedder | None,
    query: str,
    limit: int = 20,
    sort: str = "score",
) -> list[Hit]:
    terms = query_terms(query)
    rankings = [
        (KEYWORD_WEIGHT if embedder is not None else 1.0, _fts_ranking(db, terms))
    ]
    if embedder is not None:
        rankings.append((1.0, _vector_ranking(db, embedder, query)))

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
        "SELECT c.id, c.start_line, c.end_line, s.id, s.path, s.title, s.date"
        f" FROM chunks c JOIN sections s ON s.id = c.section_id WHERE c.id IN ({placeholders})",
        list(chunk_scores),
    ).fetchall()

    # One hit per section, positioned on its best chunk.
    best: dict[int, tuple[float, tuple]] = {}
    for row in rows:
        score = chunk_scores[row[0]]
        if row[3] not in best or score > best[row[3]][0]:
            best[row[3]] = (score, row)

    ranked = sorted(best.values(), key=lambda item: -item[0])[:limit]
    file_lines: dict[str, list[str]] = {}
    hits = []
    for score, (_, start, end, _, path, title, date) in ranked:
        if path not in file_lines:
            file_lines[path] = (
                Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            )
        line = _best_line(file_lines[path], start, end, terms)
        snippet = file_lines[path][line].strip() if line < len(file_lines[path]) else ""
        hits.append(Hit(path, line + 1, title, date, snippet, score))

    if sort == "date":
        hits.sort(key=lambda h: (h.date or "", h.path, h.line))
    return hits


def _fold(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(c)
    )


def _best_line(lines: list[str], start: int, end: int, terms: list[str]) -> int:
    """The line of the chunk matching the most query terms, else its first non-blank line."""
    folded_terms = [_fold(t).replace(" ", "") for t in terms]
    best, best_count = None, 0
    for i in range(start, min(end, len(lines))):
        if not lines[i].strip():
            continue
        if best is None:
            best = i
        folded = _fold(lines[i])
        # Also without punctuation, so that the compound "net 881" finds "NET-881".
        haystack = folded + " " + re.sub(r"\W+", "", folded)
        count = sum(t in haystack for t in folded_terms)
        if count > best_count:
            best, best_count = i, count
    return best if best is not None else start
