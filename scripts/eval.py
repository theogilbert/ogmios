"""How well ogmios finds and reads the day that answers a question.

    eval.py search [MODEL...]
        Rank of the expected day, per embedding model and keyword weight.
    eval.py budget [--private] [--llm] [--budgets 6000,9000,12000]
        Per budget of excerpt characters: whether the expected day is found, read, or
        cut by the budget; with --llm, whether the answer cites it (slow).

Without --private, the cases below are run against tests/fixtures/done.md. With it, the
cases are the answers marked bad with `ogmios bad --day` in the local log, run against
the configured index; only numbers are printed, never a word of a case, so the output
can be shared where the notes may not go.
"""

import argparse
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from ogmios import ask, config, journal, store
from ogmios import search as search_module
from ogmios.embed import MODELS, Embedder
from ogmios.search import search
from ogmios.when import find_period

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
    ("migration orchestrateur", "2025-01-08"),
    # jargon
    ("NET-881 approval", "2024-04-09"),
    ("RESEAU-5120", "2023-02-17"),
    ("paramiko", "2024-03-07"),
    ("blackbox exporter", "2025-02-03"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(required=True)
    s = commands.add_parser("search")
    s.add_argument("models", nargs="*", help=f"of {', '.join(MODELS)} (default: all)")
    s.set_defaults(func=eval_search)
    b = commands.add_parser("budget")
    b.add_argument("--private", action="store_true", help="cases from the local log")
    b.add_argument("--llm", action="store_true", help="also answer each case (slow)")
    b.add_argument(
        "--budgets",
        default="6000,9000,12000",
        help="excerpt characters, comma-separated (default: 6000,9000,12000)",
    )
    b.set_defaults(func=eval_budget)
    args = parser.parse_args()
    args.func(args)


def rank(hits, expected):
    return next((i + 1 for i, h in enumerate(hits) if h.date == expected), None)


def fixture_db(embedder: Embedder, tmp: str):
    db = store.connect(Path(tmp) / "index.sqlite")
    files = store.collect_files([config.Source(path=FIXTURE)])
    store.rebuild(db, files, embedder)
    return db


def eval_search(args: argparse.Namespace) -> None:
    for name in args.models or list(MODELS):
        embedder = Embedder(name, config.cache_dir() / "models")
        with tempfile.TemporaryDirectory() as tmp:
            db = fixture_db(embedder, tmp)
            for label, emb, weight in (
                ("kw=1.0", embedder, 1.0),
                ("kw=0.5", embedder, 0.5),
                ("kw=0.3", embedder, 0.3),
                ("vector", embedder, 0.0),
                ("keyword", None, 1.0),
            ):
                search_module.KEYWORD_WEIGHT = weight
                ranks = [
                    rank(search(db, emb, q, limit=10), expected)
                    for q, expected in CASES
                ]
                mrr = sum(1 / r for r in ranks if r) / len(ranks)
                print(f"{name:14} {label:8} MRR={mrr:.2f} ranks={ranks}")


def private_cases() -> list[dict]:
    """The answers marked bad with the day that holds the right one, as asked."""
    path = config.log_path()
    if path is None:
        sys.exit("logging is off (OGMIOS_LOG=off)")
    records = journal.records(path)
    asks = {r["id"]: r for r in records if r.get("type") == "ask"}
    marks = {
        r["ask"]: r["day"]
        for r in records
        if r.get("type") == "bad" and r.get("day") and r["ask"] in asks
    }
    return [
        {
            "question": asks[a]["question"],
            "day": day,
            "today": date.fromisoformat(asks[a]["today"]),
            "wordings": asks[a]["wordings"],
            "days": asks[a]["days"],
        }
        for a, day in marks.items()
    ]


def eval_budget(args: argparse.Namespace) -> None:
    budgets = [int(b) for b in args.budgets.split(",")]
    cfg = config.load(config.config_path())
    embedder = Embedder(cfg.model, config.cache_dir() / "models")
    if args.private:
        cases = private_cases()
        if not cases:
            sys.exit("no answer marked bad with --day in the log")
        db = store.connect(config.db_path())
        run(args, cfg, embedder, db, cases, budgets)
        return
    cases = [
        {"question": q, "day": d, "today": date.today(), "wordings": "", "days": 20}
        for q, d in CASES
    ]
    with tempfile.TemporaryDirectory() as tmp:
        run(args, cfg, embedder, fixture_db(embedder, tmp), cases, budgets)


def run(args, cfg, embedder, db, cases, budgets) -> None:
    llm = None
    if args.llm:
        model = ask.model_path(ask.llm_spec(cfg.llm), config.cache_dir() / "models")
        if model is None or not model.exists():
            sys.exit(f"LLM {cfg.llm} is not installed, run `ogmios install`")
        # Room for the largest budget: French takes ~2.5 characters a token.
        ask.CONTEXT_TOKENS = max(4096, int(max(budgets) / 2) + 2048)
        llm = ask.load(model)
    n = len(cases)
    header = "budget  found  read   cut  median_rank  chars"
    print(f"cases: {n}" + (f", llm: {cfg.llm}" if llm else ""))
    print(header + ("  cites  flagged  read_s" if llm else ""))
    for budget in budgets:
        ask.BUDGET_CHARS = budget
        found = read = cut = cites = flagged = 0
        ranks, chars, seconds = [], [], []
        for case in cases:
            day = case["day"]
            period, topic = find_period(case["question"], case["today"]) or (
                None,
                case["question"],
            )
            hits, excerpts = ask.gather(
                db, embedder, topic, period, case["days"], case["wordings"]
            )
            in_excerpts = any(e.date == day for e in excerpts)
            if hits:
                in_hits = (r := rank(hits, day)) is not None
                if r:
                    ranks.append(r)
            else:  # a period read whole
                in_hits = period is not None and str(period.start) <= day <= str(
                    period.end
                )
            found += in_hits
            read += in_excerpts
            cut += in_hits and not in_excerpts
            chars.append(sum(len(e.text) for e in excerpts))
            if llm and excerpts:
                chat = ask.messages(case["question"], excerpts, case["today"])
                llm.reset()  # no prompt cached from the case before
                start = time.monotonic()
                pieces = []
                for piece in ask.answer(llm, chat):
                    if not pieces:
                        seconds.append(time.monotonic() - start)
                    pieces.append(piece)
                text = "".join(pieces)
                cites += any(e.date == day for e in ask.cited(text, excerpts))
                flagged += bool(ask.unknown_dates(text, excerpts))
        median_rank = f"{statistics.median(ranks):.0f}" if ranks else "-"
        row = (
            f"{budget:<7} {found:>2}/{n:<3}{read:>2}/{n:<3}{cut:>3}"
            f"  {median_rank:>11}  {statistics.median(chars):>5.0f}"
        )
        if llm:
            read_s = f"{statistics.median(seconds):.1f}" if seconds else "-"
            row += f"  {cites:>2}/{n:<3}{flagged:>4}    {read_s:>6}"
        print(row)


if __name__ == "__main__":
    main()
