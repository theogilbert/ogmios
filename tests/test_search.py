"""Keyword-only search end to end; the embedding model is not loaded in unit tests."""

from pathlib import Path

import numpy as np
import pytest

from ogmios import store
from ogmios.config import Source
from ogmios.search import is_about_anything, notes_in, query_terms, search

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


def test_query_terms_drop_stopwords_and_fold_plurals():
    assert query_terms("when did I open the flows vers le SFTP") == [
        "open",
        "flow",
        "ver",
        "sftp",
    ]


def test_one_hit_per_day_positioned_on_matching_line(db):
    hits = search(db, None, "RESEAU-4512", sort="date")
    assert [h.date for h in hits] == ["2022-04-04", "2022-04-11", "2022-04-19"]
    first = hits[0]
    assert "RESEAU-4512" in first.snippet
    assert FIXTURE.read_text().splitlines()[first.line - 1].strip() == first.snippet


def test_accents_are_folded(db):
    assert search(db, None, "valide securite")[0].date == "2022-04-19"


def test_compounds_are_phrases():
    assert query_terms("srv-sftp-part01 and NET-881") == ["srv sftp part01", "net 881"]


def test_sort_by_date(db):
    hits = search(db, None, "NET-881", sort="date")
    assert [h.date for h in hits] == ["2024-03-14", "2024-04-02", "2024-04-09"]


def test_unchanged_index_is_not_stale(db):
    files = store.collect_files([Source(path=FIXTURE)])
    assert not store.is_stale(db, files, "fake")
    assert store.is_stale(db, files, "other-model")


def test_summary_and_changes(db, tmp_path):
    summary = store.summary(db)
    assert (summary.files, summary.first_date) == (1, "2022-03-14")
    assert summary.embeddings == {"fake": summary.chunks}
    assert store.missing_embeddings(db, "other-model") > 0
    assert store.changed_files(db, {}) == {str(FIXTURE.resolve())}


def test_notes_in_a_period(db):
    from datetime import date

    from ogmios.when import Period

    day = date(2022, 4, 11)
    hits = notes_in(db, Period(day, day, "yesterday"))
    assert [h.snippet for h in hits] == [
        "- Relance équipe réseau sur RESEAU-4512, toujours en attente de validation sécu",
        "- Point avec Julie sur le planning du sprint",
    ]
    empty = date(2022, 4, 12)
    assert notes_in(db, Period(empty, empty, "yesterday")) == []
    assert search(db, None, "RESEAU-4512", period=Period(day, day, ""))[0].line == 24


def test_queries_about_anything():
    assert is_about_anything("what did I do")
    assert is_about_anything("qu'ai-je fait ?")
    assert not is_about_anything("what did I do about sftp")


class KubernetesEmbedder:
    """Text about Kubernetes is the closest to any query."""

    name = "fake"

    def passages(self, texts):
        return np.array(
            [[1.0, 0.0] if "Kubernetes" in t else [0.0, 1.0] for t in texts]
        )

    def query(self, text):
        return np.array([1.0, 0.0])


def test_line_closest_in_meaning_when_no_word_matches(db):
    embedder = KubernetesEmbedder()
    db.execute("UPDATE embeddings SET vector = ?", (np.ones(2, np.float32).tobytes(),))
    hits = search(db, embedder, "orchestrateur")  # ty: ignore[invalid-argument-type]
    day = next(h for h in hits if h.date == "2023-01-09")
    assert "Kubernetes" in day.snippet
    assert not any(h.snippet.startswith("##") for h in hits)
