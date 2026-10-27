"""SQLite index: sections and chunks (rebuilt whenever a file changes), an FTS5 table, and
an embedding cache keyed by chunk text hash, so that only new or edited text is re-embedded."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Source
from .embed import Embedder
from .parse import chunk_section, parse_file

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    title TEXT NOT NULL,
    date TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    section_id INTEGER NOT NULL REFERENCES sections(id),
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    body, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS embeddings (
    model TEXT NOT NULL,
    hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (model, hash)
);
"""


@dataclass
class IndexStats:
    files: int
    sections: int
    chunks: int
    embedded: int


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def collect_files(sources: list[Source]) -> dict[Path, Source]:
    """Files to index, each with the first source that lists it."""
    files: dict[Path, Source] = {}
    for source in sources:
        for f in source.files():
            files.setdefault(f.resolve(), source)
    return files


@dataclass
class Summary:
    files: int
    sections: int
    dated: int
    chunks: int
    first_date: str | None
    last_date: str | None
    embeddings: dict[str, int]  # vectors cached per model


def is_stale(db: sqlite3.Connection, files: dict[Path, Source], model: str) -> bool:
    # A model change leaves chunks without embeddings.
    return bool(changed_files(db, files)) or missing_embeddings(db, model) > 0


def changed_files(db: sqlite3.Connection, files: dict[Path, Source]) -> set[str]:
    """Files added, edited or removed since the last index."""
    stored = {
        row[0]: (row[1], row[2])
        for row in db.execute("SELECT path, mtime_ns, size FROM files")
    }
    current = {}
    for f in files:
        st = f.stat()
        current[str(f)] = (st.st_mtime_ns, st.st_size)
    return {
        p for p in stored.keys() | current.keys() if stored.get(p) != current.get(p)
    }


def missing_embeddings(db: sqlite3.Connection, model: str) -> int:
    return db.execute(
        "SELECT count(DISTINCT c.hash) FROM chunks c"
        " LEFT JOIN embeddings e ON e.hash = c.hash AND e.model = ?"
        " WHERE e.hash IS NULL",
        (model,),
    ).fetchone()[0]


def summary(db: sqlite3.Connection) -> Summary:
    files = db.execute("SELECT count(*) FROM files").fetchone()[0]
    sections, dated, first, last = db.execute(
        "SELECT count(*), count(date), min(date), max(date) FROM sections"
    ).fetchone()
    chunks = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
    embeddings = dict(
        db.execute(
            "SELECT model, count(*) FROM embeddings GROUP BY model ORDER BY model"
        )
    )
    return Summary(files, sections, dated, chunks, first, last, embeddings)


def rebuild(
    db: sqlite3.Connection, files: dict[Path, Source], embedder: Embedder
) -> IndexStats:
    """Re-parse every file (cheap) and embed only chunks whose text is not cached yet."""
    with db:
        db.execute("DELETE FROM chunks_fts")
        db.execute("DELETE FROM chunks")
        db.execute("DELETE FROM sections")
        db.execute("DELETE FROM files")
        n_sections = 0
        for path, source in files.items():
            st = path.stat()
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            db.execute(
                "INSERT INTO files VALUES (?, ?, ?)",
                (str(path), st.st_mtime_ns, st.st_size),
            )
            for section in parse_file(path, lines, source.options):
                n_sections += 1
                cur = db.execute(
                    "INSERT INTO sections (path, start_line, end_line, title, date) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(path),
                        section.start,
                        section.end,
                        section.title,
                        section.date,
                    ),
                )
                for chunk in chunk_section(lines, section):
                    embed_text = chunk.embed_text
                    digest = hashlib.sha1(embed_text.encode()).hexdigest()
                    cur2 = db.execute(
                        "INSERT INTO chunks (section_id, kind, start_line, end_line, text, hash)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            cur.lastrowid,
                            chunk.kind,
                            chunk.start,
                            chunk.end,
                            embed_text,
                            digest,
                        ),
                    )
                    db.execute(
                        "INSERT INTO chunks_fts (rowid, body) VALUES (?, ?)",
                        (cur2.lastrowid, embed_text),
                    )

        todo = db.execute(
            "SELECT DISTINCT c.hash, c.text FROM chunks c"
            " LEFT JOIN embeddings e ON e.hash = c.hash AND e.model = ?"
            " WHERE e.hash IS NULL",
            (embedder.name,),
        ).fetchall()
        if todo:
            vectors = embedder.passages(text for _, text in todo)
            db.executemany(
                "INSERT INTO embeddings (model, hash, vector) VALUES (?, ?, ?)",
                [(embedder.name, h, v.tobytes()) for (h, _), v in zip(todo, vectors)],
            )
        # Forget embeddings of text that no longer exists.
        db.execute(
            "DELETE FROM embeddings WHERE model = ? AND hash NOT IN (SELECT hash FROM chunks)",
            (embedder.name,),
        )
        n_chunks = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
    return IndexStats(len(files), n_sections, n_chunks, len(todo))
