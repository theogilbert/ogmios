"""How well ogmios finds and reads the day that answers a question.

    eval.py search [MODEL...]
        Rank of the expected day, per embedding model and keyword weight.
    eval.py budget [--private] [--llm] [--budgets 6000,9000,12000]
        Per budget of excerpt characters: whether the expected day is found, read, or
        cut by the budget; with --llm, whether the answer cites it (slow).
    eval.py scan
        On tests/fixtures/long.md and synonyms.md, subjects over many days, with the model: `ask
        --first` and `--all` against one read of the notes, per budget.

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

from ogmios import ask, config, journal, scan, store
from ogmios import search as search_module
from ogmios.embed import MODELS, Embedder
from ogmios.search import search
from ogmios.when import find_period

FIXTURE = Path(__file__).parent.parent / "tests/fixtures/done.md"
LONG_FIXTURES = [FIXTURE.with_name("long.md"), FIXTURE.with_name("synonyms.md")]
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


# In long.md. The first days of a subject only talk of it, as an idea, then it is done.
TERRAFORM_IDEA = ["2022-05-11", "2022-06-20"]
TERRAFORM_DONE = [
    "2022-09-12", "2022-09-29", "2022-10-17", "2022-11-08", "2022-12-05", "2023-01-23",
    "2023-02-27", "2023-03-20", "2023-04-24", "2023-06-05", "2023-07-10", "2023-09-04",
    "2023-10-16", "2023-11-20", "2024-01-15", "2024-02-19", "2024-04-08", "2024-05-27",
    "2024-07-01", "2024-09-09", "2024-10-21", "2024-11-25",
]  # fmt: skip
KAFKA_IDEA = ["2023-02-06", "2023-03-13"]
KAFKA_DONE = [
    "2023-05-15", "2023-06-26", "2023-08-07", "2023-09-18", "2023-12-04", "2024-01-29",
    "2024-03-25", "2024-06-17", "2024-08-12", "2024-12-09",
]  # fmt: skip
FIRST_CASES = [
    ("when was the first time I talked about terraform?", "2022-05-11"),
    ("when was the first time I applied terraform on our projects?", "2022-09-12"),
    ("quelle a été la première fois que j'ai parlé de kafka ?", "2023-02-06"),
    ("when did I first deploy kafka in production?", "2023-05-15"),
    ("la première fois que j'ai utilisé vault", "2022-04-13"),
    ("first time I talked about ansible", None),  # never
    # Never in the words of the question: not to tune on, or they stop saying anything.
    ("when was the first time I dealt with SSL certificates?", "2022-06-14"),
    ("la première fois que j'ai travaillé sur les sauvegardes", "2022-07-20"),
    ("when was the first time I worked on recruiting?", "2022-11-22"),
    ("la première fois que j'ai voyagé pour le travail", "2023-10-05"),
]
ALL_CASES = [
    ("list every time I worked on terraform", TERRAFORM_DONE, TERRAFORM_IDEA),
    ("toutes les fois où j'ai travaillé sur kafka", KAFKA_DONE, KAFKA_IDEA),
    ("every time I worked on ansible", [], []),  # never
    (
        "list every time I dealt with SSL certificates",
        ["2022-06-14", "2023-03-08", "2023-07-19", "2024-02-07", "2024-10-02"],
        [],
    ),
    (
        "toutes les fois où j'ai travaillé sur les sauvegardes",
        ["2022-07-20", "2023-04-12", "2023-11-02", "2024-05-15"],
        [],
    ),
    (
        "list every time I worked on recruiting",
        ["2022-11-22", "2023-05-30", "2024-06-11"],
        [],
    ),
    (
        "toutes les fois où j'ai voyagé pour le travail",
        ["2023-10-05", "2024-03-19", "2024-09-24"],
        [],
    ),
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
    c = commands.add_parser("scan")
    c.set_defaults(func=eval_scan)
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


def eval_scan(args: argparse.Namespace) -> None:
    cfg = config.load(config.config_path())
    embedder = Embedder(cfg.model, config.cache_dir() / "models")
    model = ask.model_path(ask.llm_spec(cfg.llm), config.cache_dir() / "models")
    if model is None or not model.exists():
        sys.exit(f"LLM {cfg.llm} is not installed, run `ogmios install`")
    ask.CONTEXT_TOKENS = 8192  # room for the largest single read
    llm = ask.load(model)
    today = date.today()
    with tempfile.TemporaryDirectory() as tmp:
        db = store.connect(Path(tmp) / "index.sqlite")
        sources = [config.Source(path=p) for p in LONG_FIXTURES]
        store.rebuild(db, store.collect_files(sources), embedder)

        def wordings(topic: str) -> str:
            llm.reset()
            return ask.other_wordings(llm, topic)

        def once(question: str, budget: int) -> tuple[set[str], float]:
            ask.BUDGET_CHARS = budget
            _, excerpts = ask.gather(
                db, embedder, question, None, 20, wordings(question)
            )
            llm.reset()
            start = time.monotonic()
            text = "".join(ask.answer(llm, ask.messages(question, excerpts, today)))
            days = {e.date for e in ask.cited(text, excerpts) if e.date}
            return days, time.monotonic() - start

        def scanned(question: str, mode: str) -> tuple[scan.Scan, float]:
            ask.BUDGET_CHARS = 6_000
            topic = scan.subject(question)
            found = wordings(topic)
            hits, days = scan.candidates(db, embedder, topic, mode, None, None, found)
            state = scan.Scan(mode, topic, found, scan.excerpts_for(db, hits), days)
            llm.reset()
            start = time.monotonic()
            for _ in scan.run(llm, state):
                pass
            return state, time.monotonic() - start

        print("first time: expected day, then what each answers")
        for question, expected in FIRST_CASES:
            state, seconds = scanned(question, scan.FIRST)
            first = scan.first_day(state)
            got = first.excerpt.date if first else None
            singles = []
            for budget in (6_000, 12_000):
                days, s = once(question, budget)
                single = min(days, default=None)
                singles.append(
                    f"{budget}: {single} {'ok' if single == expected else 'WRONG'} {s:.0f}s"
                )
            mark = "ok" if got == expected else "WRONG"
            print(f"  {question}\n    expected {expected}")
            print(f"    scan: {got} {mark} {seconds:.0f}s, ({scan.coverage(state)})")
            print(f"    once: {'; '.join(singles)}")

        print("\nevery time: days of the expected found, days that should not be")
        for question, done, idea in ALL_CASES:
            state, seconds = scanned(question, scan.ALL)
            allowed = set(done) | set(idea)
            yes = {v.excerpt.date for v in state.yes}
            print(f"  {question}")
            print(
                f"    scan: {len(yes & set(done))}/{len(done)}, wrong"
                f" {len(yes - allowed)}, {seconds:.0f}s,"
                f" {state.checked}/{len(state.excerpts)} days,"
                f" ({state.stopped or 'read all'})"
            )
            for budget in (6_000, 12_000):
                days, s = once(question, budget)
                print(
                    f"    once {budget}: {len(days & set(done))}/{len(done)},"
                    f" wrong {len(days - allowed)}, {s:.0f}s"
                )


if __name__ == "__main__":
    main()
