"""Rank of the expected day for a set of queries, per model (hybrid and vector-only)."""

import sys
import tempfile
from pathlib import Path

from ogmios import config, store
from ogmios.embed import MODELS, Embedder
from ogmios.search import search

FIXTURE = Path(__file__).parent.parent / "tests/fixtures/done.md"
CASES = [
    (
        "when did I request network flows to be open between this sftp server and our airflow instance?",
        "2022-04-04",
    ),
    ("TLS outage", "2025-02-03"),
    ("cost of the data warehouse", "2024-03-12"),
    ("performance review with my boss", "2023-02-01"),
    ("pods restarting because of health checks", "2024-03-06"),
    ("partner changed their key without telling us", "2023-01-05"),
    ("demande d'ouverture firewall pour le monitoring", "2023-02-17"),
    ("security wanted a justification for the IP ranges", "2024-04-02"),
    ("quand est-ce que j'ai upgradé airflow en prod", "2024-03-14"),
    ("which python library broke the sftp operator", "2024-03-07"),
    ("first day at the company", "2022-03-14"),
    ("plan to get rid of the old managed airflow", "2025-01-08"),
    # jargon
    ("NET-881 approval", "2024-04-09"),
    ("RESEAU-5120", "2023-02-17"),
    ("paramiko", "2024-03-07"),
    ("blackbox exporter", "2025-02-03"),
]


def rank(hits, expected):
    return next((i + 1 for i, h in enumerate(hits) if h.date == expected), None)


from ogmios import search as search_module

models = sys.argv[1:] or list(MODELS)
sources = [config.Source(path=FIXTURE)]
for name in models:
    embedder = Embedder(name, config.cache_dir() / "models")
    with tempfile.TemporaryDirectory() as tmp:
        db = store.connect(Path(tmp) / "index.sqlite")
        store.rebuild(db, store.collect_files(sources), embedder)
        for label, emb, weight in (
            ("kw=1.0", embedder, 1.0),
            ("kw=0.5", embedder, 0.5),
            ("kw=0.3", embedder, 0.3),
            ("vector", embedder, 0.0),
            ("keyword", None, 1.0),
        ):
            if label == "keyword" and name != models[0]:
                continue
            search_module.KEYWORD_WEIGHT = weight
            ranks = [
                rank(search(db, emb, q, limit=10), expected) for q, expected in CASES
            ]
            mrr = sum(1 / r for r in ranks if r) / len(ranks)
            print(f"{name:14} {label:8} MRR={mrr:.2f} ranks={ranks}")
