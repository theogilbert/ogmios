import argparse
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

from . import ask, config, gitlab, hub, journal, scan, store
from .embed import MODELS, Embedder
from .parse import SplitOptions, chunk_section, parse_file
from .search import is_about_anything
from .spinner import Spinner
from .when import find_period

DEFAULT_DAYS = 20


def main() -> None:
    args = parse_cli_args()
    try:
        args.func(args)
    except BrokenPipeError:
        pass  # output piped into a picker that was closed early
    except (ValueError, OSError) as e:
        print(f"ogmios: {e}", file=sys.stderr)
        sys.exit(1)


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ogmios", description="Ask your notes.")
    parser.add_argument(
        "--config", type=Path, default=config.config_path(), help="config file"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=config.db_path(),
        help="index database",
    )
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        help="file or directory of notes, instead of the configured sources (repeatable)",
    )
    parser.add_argument("--model", choices=sorted(MODELS), help="embedding model")
    commands = parser.add_subparsers(required=True)

    install = commands.add_parser(
        "install",
        help="download the models and configure ogmios to use them",
        description="Download the embedding model (--model, else the configured one)"
        " and the LLM (--llm, else the configured one), then write both to the config"
        " file, the LLM pinned to the revision downloaded.",
    )
    install.add_argument(
        "--llm",
        metavar="LLM",
        help=f"the LLM to use: {', '.join(ask.LLMS)}, or a GGUF file on Hugging Face,"
        " as REPO/FILE.gguf or its URL",
    )
    install.set_defaults(func=cmd_install)

    index = commands.add_parser("index", help="(re)build the index")
    index.add_argument(
        "--force", action="store_true", help="rebuild even if nothing changed"
    )
    index.set_defaults(func=cmd_index)

    question = commands.add_parser(
        "ask",
        help="answer a question from the notes (refreshes the index first)",
    )
    question.add_argument("question", nargs="+")
    question.add_argument(
        "-s",
        "--source",
        action="append",
        metavar="NAME",
        help="search only this source (repeatable; default: all of them)",
    )
    question.add_argument(
        "-n",
        "--days",
        type=int,
        help="most days of notes to read, as many as fit (default: 20); with --first"
        " and --all, the days to check (default: those found, up to 100)",
    )
    kind = question.add_mutually_exclusive_group()
    kind.add_argument(
        "--all",
        dest="mode",
        action="store_const",
        const=scan.ALL,
        help="list every time it happened: checks the days found one by one, up to 100"
        " (~0.5-3 s a day)",
    )
    kind.add_argument(
        "--first",
        dest="mode",
        action="store_const",
        const=scan.FIRST,
        help="find the first time it happened: checks the days found one by one, oldest"
        " first, until one shows it (~0.5-3 s a day)",
    )
    question.add_argument(
        "--prompt",
        action="store_true",
        help="print what the model would be given, without the answer",
    )
    question.add_argument(
        "--literal",
        action="store_true",
        help="search the question's own words only, not other wordings the model"
        " thinks of (~2 s faster)",
    )
    question.add_argument(
        "--json",
        action="store_true",
        help="print the answer and its sources as one JSON object, once written",
    )
    question.set_defaults(func=cmd_ask)

    bad = commands.add_parser(
        "bad",
        help="mark the last answer as wrong, for `ogmios report`",
        description="Mark the last answer as wrong, in the log. Given the day of the"
        " notes that holds the right answer, says why it went wrong: the search did not"
        " find that day, the budget left it out, or the model misread it.",
    )
    bad.add_argument(
        "--day", type=date.fromisoformat, help="the day holding the right answer"
    )
    bad.add_argument("note", nargs="*", help="what was wrong, in your own words")
    bad.set_defaults(func=cmd_bad)

    report = commands.add_parser(
        "report",
        help="statistics of the questions logged, without a word of the notes",
        description="Statistics of the questions logged: counts, rates and measures"
        " only, never a word of a question, a note, an answer or a path. Read it before"
        " you share it.",
    )
    report.set_defaults(func=cmd_report)

    inspect = commands.add_parser("inspect", help="show how a file is split")
    inspect.add_argument("file", type=Path)
    inspect.add_argument("--chunks", action="store_true", help="also print every chunk")
    inspect.set_defaults(func=cmd_inspect)

    status = commands.add_parser(
        "status", help="show where files are stored and the state of the index"
    )
    status.set_defaults(func=cmd_status)

    fetch = commands.add_parser(
        "fetch",
        help="update the mirror of fetched sources, such as GitLab issues",
        description="Fetch what changed since the last fetch in sources that are not"
        " local files: the issues of a GitLab project. The token is read from"
        " $GITLAB_TOKEN, else asked for; it is never stored.",
    )
    fetch.add_argument("name", nargs="*", help="only these sources (default: all)")
    fetch.add_argument(
        "--full",
        action="store_true",
        help="fetch everything again, and forget what was deleted since",
    )
    fetch.set_defaults(func=cmd_fetch)

    src = commands.add_parser("source", help="list, add or remove sources")
    src_commands = src.add_subparsers(required=True)
    listing = src_commands.add_parser("list", help="list the sources")
    listing.set_defaults(func=cmd_source_list)
    remove = src_commands.add_parser(
        "remove", help="stop indexing a source (and delete its mirror, if fetched)"
    )
    remove.add_argument("name")
    remove.set_defaults(func=cmd_source_remove)
    src_add = src_commands.add_parser("add", help="add a source")
    add_types = src_add.add_subparsers(required=True, metavar="TYPE")
    add = add_types.add_parser(
        config.LOCAL, help="a file or directory of notes, read where it is"
    )
    add.add_argument("path", type=Path)
    add.add_argument("--name", help="what to call it (default: the file or directory)")
    add.add_argument("--glob", help="files to index in a directory (default: **/*.md)")
    add.add_argument(
        "--exclude",
        action="append",
        metavar="GLOB",
        help="files to skip, relative to the directory (repeatable)",
    )
    add.add_argument(
        "--split",
        choices=["auto", "dates", "headings"],
        help="split on date lines or headings (default: auto)",
    )
    add.add_argument(
        "--delimiter",
        action="append",
        metavar="REGEX",
        help="regex selecting the date lines, if auto-detection fails (repeatable)",
    )
    add.add_argument(
        "--month-first", action="store_true", help="03/04/2024 is March the 4th"
    )
    add.set_defaults(func=cmd_source_add_local)
    add_gitlab = add_types.add_parser(
        config.GITLAB_ISSUES,
        help="the issues of a GitLab project, mirrored by `ogmios fetch`",
    )
    add_gitlab.add_argument("url", help="the project's URL, https://HOST/GROUP/PROJECT")
    add_gitlab.add_argument(
        "--name", help="what to call it (default: the project's name)"
    )
    add_gitlab.set_defaults(func=cmd_source_add_gitlab)

    cfg = commands.add_parser("config", help="change the configuration")
    cfg_commands = cfg.add_subparsers(required=True)
    model = cfg_commands.add_parser("model", help="show or set the embedding model")
    model.add_argument("name", nargs="?", choices=sorted(MODELS))
    model.set_defaults(func=cmd_config_model)
    llm = cfg_commands.add_parser(
        "llm",
        help="show or set the LLM",
        description="Set the LLM: a built-in one, or any GGUF file on Hugging Face, as"
        " REPO/FILE.gguf[@REVISION] or its URL. `ogmios install` then downloads it and,"
        " without a revision, pins the one it is at.",
    )
    llm.add_argument("name", nargs="?", metavar="LLM")
    llm.set_defaults(func=cmd_config_llm)
    threads = cfg_commands.add_parser(
        "threads",
        help="show or set the most CPU threads to use",
        description="Show or set the most CPU threads ogmios uses, for embeddings and the"
        " LLM; 0 for no cap. $OGMIOS_THREADS overrides it.",
    )
    threads.add_argument("number", nargs="?", type=int)
    threads.set_defaults(func=cmd_config_threads)
    show = cfg_commands.add_parser("show", help="print the config file")
    show.set_defaults(func=cmd_config_show)
    edit = cfg_commands.add_parser("edit", help="open the config file in $EDITOR")
    edit.set_defaults(func=cmd_config_edit)

    return parser.parse_args()


def _config(args: argparse.Namespace) -> config.Config:
    cfg = config.load(args.config)
    if args.path:
        cfg.sources = [
            config.Source(path=p.expanduser(), name=config.default_name(p))
            for p in args.path
        ]
    return cfg


def _load(args: argparse.Namespace) -> tuple[config.Config, Embedder]:
    cfg = _config(args)
    if not cfg.sources:
        raise ValueError("no sources: add one with `ogmios source add`, or pass --path")
    embedder = Embedder(
        args.model or cfg.model, config.cache_dir() / "models", cfg.threads
    )
    # Before the index is touched, rather than halfway through re-embedding it.
    if not embedder.downloaded:
        raise ValueError(
            f"embedding model {embedder.name} is not installed, run `ogmios install`"
        )
    return cfg, embedder


def _refresh(
    args: argparse.Namespace,
    cfg: config.Config,
    embedder: Embedder,
    force: bool = False,
    spinner: Spinner | None = None,
):
    db = store.connect(args.db)
    files = store.collect_files(cfg.sources)
    if force or store.is_stale(db, files, embedder.name):
        if spinner:
            spinner.status("indexing the notes")
        stats = store.rebuild(db, files, embedder)
        (spinner or Spinner(enabled=False)).print(
            f"indexed {stats.files} files: {stats.sections} sections, {stats.chunks} chunks"
            f" ({stats.embedded} newly embedded)"
        )
    return db


def cmd_install(args: argparse.Namespace) -> None:
    cfg = config.load(args.config)
    models = config.cache_dir() / "models"
    embedder = Embedder(args.model or cfg.model, models)
    llm_value = ask.llm_value(args.llm) if args.llm else cfg.llm
    llm = ask.llm_spec(llm_value)
    if llm.revision is None:
        llm = replace(llm, revision=hub.latest_revision(llm.repo))
        llm_value = llm.value()
    assert llm.revision is not None
    spec = embedder.spec
    wanted = [
        (
            f"embedding model {embedder.name}",
            spec.repo,
            spec.revision,
            embedder.dir,
            embedder.files,
        ),
        (
            f"LLM {llm_value if llm_value in ask.LLMS else llm.model_file}",
            llm.repo,
            llm.revision,
            hub.model_dir(models, llm.repo, llm.revision),
            [llm.model_file],
        ),
    ]

    for what, repo, revision, directory, files in wanted:
        print(f"{what}  (huggingface.co/{repo})")
        print(f"  into {config.tilde(directory)}")
        missing = [f for f in files if not (directory / f).exists()]
        if not missing:
            print("  installed")
            continue
        sizes = [hub.remote_size(repo, revision, f) for f in missing]
        if None in sizes and not any(
            (directory / (f + ".part")).exists() for f in missing
        ):
            # Most likely a misspelt file name; let the download say for sure.
            print(f"  could not get the size of {', '.join(missing)}")
        size = (
            "size unknown" if None in sizes else hub.human(sum(s or 0 for s in sizes))
        )
        parts = [directory / (f + ".part") for f in missing]
        resumed = sum(p.stat().st_size for p in parts if p.exists())
        note = f", {hub.human(resumed)} already downloaded" if resumed else ""
        print(f"  downloading {size}{note}", flush=True)
        for f in missing:
            hub.download(repo, revision, f, directory)

    config.set_value(args.config, "model", embedder.name)
    config.set_value(args.config, "llm", llm_value)
    print(
        f'\nset model = "{embedder.name}", llm = "{llm_value}"'
        f" in {config.tilde(args.config)}"
    )


def cmd_index(args: argparse.Namespace) -> None:
    cfg, embedder = _load(args)
    _refresh(args, cfg, embedder, force=args.force)


def cmd_ask(args: argparse.Namespace) -> None:
    cfg, embedder = _load(args)
    model = ask.model_path(ask.llm_spec(cfg.llm), config.cache_dir() / "models")
    if not (args.prompt and args.literal):  # the only case that does not run it
        model = hub.installed(model, f"LLM {cfg.llm}")
    question = " ".join(args.question)
    # Not over --json or a pipe: whatever reads stderr there wants lines, not frames.
    interactive = sys.stderr.isatty() and not (args.json or args.prompt)
    with Spinner(enabled=interactive) as spinner:
        _ask(args, cfg, embedder, model, question, spinner)


def _ask(
    args: argparse.Namespace,
    cfg: config.Config,
    embedder: Embedder,
    model: Path | None,  # None only with --prompt --literal, which do not run it
    question: str,
    spinner: Spinner,
) -> None:
    clock = time.monotonic()
    seconds: dict[str, float] = {}

    def lap(step: str) -> None:
        nonlocal clock
        now = time.monotonic()
        seconds[step] = seconds.get(step, 0.0) + now - clock
        clock = now

    started = clock
    paths = _scope(cfg, args.source)
    db = _refresh(args, cfg, embedder, spinner=spinner)
    lap("index")
    today = date.today()
    period, topic = find_period(question, today) or (None, question)
    if args.mode:
        topic = scan.subject(topic)
        if is_about_anything(topic):
            raise ValueError(f"--{args.mode} needs a subject to look for")
    whole = not args.mode and period is not None and is_about_anything(topic)
    llm, wordings = None, ""
    if not whole and not args.literal:
        spinner.status("loading the model")
        llm = ask.load(model, cfg.threads)  # ty: ignore[invalid-argument-type]
        lap("load")
        spinner.status("thinking of other wordings")
        wordings = ask.other_wordings(llm, topic)
        lap("wordings")
    if not whole:
        spinner.status("searching the notes")
    if args.mode:
        hits, found = scan.candidates(
            db, embedder, topic, args.mode, period, args.days, wordings, paths
        )
        excerpts = scan.excerpts_for(db, hits)
    else:
        hits, excerpts = ask.gather(
            db, embedder, topic, period, args.days or DEFAULT_DAYS, wordings, paths
        )
    lap("search")
    if not excerpts:
        where = f" from {period} ({period.words})" if period else ""
        raise ValueError(f"no notes{where} to answer from")
    excerpts = _with_urls(cfg, excerpts)
    if args.mode:
        if args.prompt:
            _print_scan_prompt(topic, wordings, excerpts)
            return
        if llm is None:
            spinner.status("loading the model")
            llm = ask.load(model, cfg.threads)  # ty: ignore[invalid-argument-type]
            lap("load")
        state = scan.Scan(args.mode, topic, wordings, excerpts, found)
        run = scan.run(
            llm,
            state,
            lambda number, total: spinner.status(f"checking day {number} of {total}"),
        )
        lines: list[str] = []
        for verdict in run:
            if scan.shown(state, verdict):
                lines.append(scan.line(state, verdict))
                if not args.json:
                    spinner.answer(lines[-1])
        spinner.stop()
        lap("scan")
        seconds["total"] = time.monotonic() - started
        closing = scan.conclusion(state)
        text = "\n".join([*lines, *closing]).strip()
        _log(
            question=question,
            today=today,
            period=period,
            whole=False,
            wordings=wordings,
            literal=args.literal,
            days=len(hits),
            llm=cfg.llm,
            model=embedder.name,
            hits=hits,
            excerpts=state.read,
            chat=[m for chat in state.chats for m in chat],
            answer=text,
            prompt_tokens=sum(ask.prompt_tokens(llm, c) for c in state.chats),
            answer_tokens=state.tokens,
            seconds=seconds,
            mode=args.mode,
        )
        if args.json:
            result = ask.result(question, text, state.read, wordings)
            result |= {"mode": args.mode, "coverage": scan.coverage(state)}
            print(json.dumps(result, ensure_ascii=False))
            return
        print("\n".join(closing).strip("\n") if not lines else "\n".join(closing))
        _print_cited(text, state.read)
        return
    chat = ask.messages(question, excerpts, today)
    if args.prompt:
        if wordings:
            print(f"--- searched besides the question\n{wordings}")
        for message in chat:
            print(f"--- {message['role']}\n{message['content']}")
        return

    dates = sorted(e.date for e in excerpts if e.date)
    span = f", {dates[0]} to {dates[-1]}" if dates else ""
    reading = f"reading {_plural(len(excerpts), 'excerpt')}{span}"
    if not spinner.enabled:
        print(f"ogmios: {reading}", file=sys.stderr)
    if llm is None:
        spinner.status("loading the model")
        llm = ask.load(model, cfg.threads)  # ty: ignore[invalid-argument-type]
        lap("load")
    spinner.status(reading)
    written: list[str] = []
    for piece in ask.answer(llm, chat):
        if not written:
            lap("read")  # nothing comes until the model has read it all
            spinner.stop()
        written.append(piece)
        if not args.json:
            print(piece, end="", flush=True)
    lap("write")
    seconds["total"] = time.monotonic() - started
    text = "".join(written)
    _log(
        question=question,
        today=today,
        period=period,
        whole=whole,
        wordings=wordings,
        literal=args.literal,
        days=args.days or DEFAULT_DAYS,
        llm=cfg.llm,
        model=embedder.name,
        hits=hits,
        excerpts=excerpts,
        chat=chat,
        answer=text.strip(),
        prompt_tokens=ask.prompt_tokens(llm, chat),
        answer_tokens=len(written),
        seconds=seconds,
    )
    if args.json:
        result = ask.result(question, text.strip(), excerpts, wordings)
        print(json.dumps(result, ensure_ascii=False))
        return
    print(flush=True)  # before the warnings, which go to stderr
    for wrong in ask.unknown_dates(text, excerpts):
        print(
            f"ogmios: {wrong} is not a date of the notes read, check it",
            file=sys.stderr,
        )
    _print_cited(text, excerpts)


def _print_cited(text: str, excerpts: list[ask.Excerpt]) -> None:
    sources = ask.cited(text, excerpts)
    if sources:
        print()
    for i, e in enumerate(excerpts, 1):
        if e in sources:
            where = e.url or f"{e.path}:{e.line}"
            print(f"[{i}] {e.date or e.title} | {where}")


def _print_scan_prompt(topic: str, wordings: str, excerpts: list[ask.Excerpt]) -> None:
    print(f"--- asked about\n{topic}")
    if wordings:
        print(f"--- searched besides it\n{wordings}")
    print(f"--- system, for each day\n{scan.SYSTEM}")
    for i, excerpt in enumerate(excerpts[: scan.MAX_DAYS], 1):
        print(f"--- day {i}\n{scan.messages(topic, excerpt)[1]['content']}")


def _scope(cfg: config.Config, names: list[str] | None) -> list[str] | None:
    """The files of the sources named, or None for all of them."""
    if not names:
        return None
    by_name = {s.name: s for s in cfg.sources}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ValueError(
            f"no source named {', '.join(map(repr, unknown))}"
            f" (sources: {', '.join(by_name) or 'none'})"
        )
    files = store.collect_files(cfg.sources)
    return [str(f) for f, source in files.items() if source.name in names]


def _with_urls(cfg: config.Config, excerpts: list[ask.Excerpt]) -> list[ask.Excerpt]:
    """Excerpts of mirrored issues point at the issue on the web."""
    mirrors = [s.path.resolve() for s in cfg.sources if s.type == config.GITLAB_ISSUES]
    return [
        replace(e, url=gitlab.issue_url(Path(e.path)))
        if any(Path(e.path).is_relative_to(m) for m in mirrors)
        else e
        for e in excerpts
    ]


def _log(**fields) -> None:
    """Logs the question, unless logging is off; a log that cannot be written does not
    lose the answer."""
    path = config.log_path()
    if path is None:
        return
    try:
        journal.append(path, journal.ask_record(**fields))
    except (OSError, ValueError) as e:
        print(f"ogmios: not logged: {e}", file=sys.stderr)


def cmd_bad(args: argparse.Namespace) -> None:
    path = _log_path()
    last = journal.last_ask(path)
    day = args.day.isoformat() if args.day else None
    journal.append(path, journal.bad_record(last, day, " ".join(args.note)))
    print(f"marked bad: {last['question']}  (asked {last['at'].replace('T', ' ')})")
    cause, rank = journal.diagnose(last, day)
    ranked = f", ranked {rank} of {len(last['hits'])} by the search" if rank else ""
    explanations = {
        "not found": "the search did not find that day: try the words of the note,"
        " a time frame, or a ticket or project name",
        "outside the period": "that day is outside the period the question named",
        "cut by the budget": "the search found that day, but the budget left it out",
        "misread": "the model read that day and got it wrong",
    }
    if cause != "unknown":
        print(f"{explanations[cause]}{ranked}")


def cmd_report(args: argparse.Namespace) -> None:
    print(journal.report(journal.records(_log_path())))


def _log_path() -> Path:
    path = config.log_path()
    if path is None:
        raise ValueError("logging is off (OGMIOS_LOG=off)")
    return path


def cmd_inspect(args: argparse.Namespace) -> None:
    cfg = config.load(args.config)
    path = args.file.expanduser().resolve()
    options = SplitOptions()
    for source in cfg.sources:
        if path in {f.resolve() for f in source.files()}:
            options = source.options
            break
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    sections = parse_file(path, lines, options)
    dated = sum(s.date is not None for s in sections)
    print(f"{path}: {len(sections)} sections ({dated} dated)")
    for section in sections:
        print(f"L{section.start + 1:<6} {section.title}")
        for block in section.blocks:
            print(f"  L{block.start + 1:<6} {block.title}")
        if args.chunks:
            for chunk in chunk_section(lines, section):
                body = re.sub(r"\s+", " ", chunk.text)
                print(f"    [{chunk.kind} L{chunk.start + 1}-{chunk.end}] {body[:100]}")


def cmd_status(args: argparse.Namespace) -> None:
    cfg = _config(args)
    embedder = Embedder(args.model or cfg.model, config.cache_dir() / "models")

    def overridden(variable: str) -> str:
        return f"  (from ${variable})" if variable in os.environ else ""

    print("paths")
    config_note = "" if args.config.exists() else "  (not found)"
    print(
        f"  config  {config.tilde(args.config)}{config_note}{overridden('OGMIOS_CONFIG')}"
    )
    db_note = f"  ({_size(args.db)})" if args.db.exists() else "  (not created yet)"
    print(f"  index   {config.tilde(args.db)}{db_note}{overridden('OGMIOS_DB')}")
    print(f"  models  {config.tilde(config.cache_dir() / 'models')}")
    log = config.log_path()
    if log is None:
        print(f"  log     off{overridden('OGMIOS_LOG')}")
    else:
        files = [f for f in (journal.previous(log), log) if f.exists()]
        size = hub.human(sum(f.stat().st_size for f in files)) if files else "empty"
        print(f"  log     {config.tilde(log)}  ({size}){overridden('OGMIOS_LOG')}")

    print(f"\nthreads  {cfg.threads or 'no cap'}{overridden('OGMIOS_THREADS')}")
    print(f"\nmodel  {embedder.name}  ({embedder.spec.repo})")
    if embedder.downloaded:
        print(f"  {config.tilde(embedder.dir)}  ({_size(embedder.dir)})")
    else:
        print("  not installed, run `ogmios install`")
    llm = ask.model_path(ask.llm_spec(cfg.llm), config.cache_dir() / "models")
    print(f"\nllm  {cfg.llm}")
    if llm is None or not llm.exists():
        print("  not installed, run `ogmios install`")
    else:
        print(f"  {config.tilde(llm)}  ({_size(llm)})")

    print(
        "\nsources"
        + ("" if cfg.sources else "  none, add one with `ogmios source add`")
    )
    _print_sources(cfg.sources)

    if not args.db.exists():
        return
    db = store.connect(args.db)
    s = store.summary(db)
    print(
        f"\nindex  {_plural(s.files, 'file')}, {_plural(s.sections, 'section')}"
        f" ({s.dated} dated), {_plural(s.chunks, 'chunk')}"
    )
    if s.first_date:
        print(f"  dates   {s.first_date} to {s.last_date}")
    for model, n in s.embeddings.items():
        print(f"  vectors {n} for {model}")
    changed = store.changed_files(db, store.collect_files(cfg.sources))
    missing = store.missing_embeddings(db, embedder.name)
    if not changed and not missing:
        print("  up to date")
    else:
        reasons = []
        if changed:
            reasons.append(f"{_plural(len(changed), 'file')} changed")
        if missing:
            reasons.append(
                f"{_plural(missing, 'chunk')} not embedded with {embedder.name}"
            )
        print(f"  stale: {', '.join(reasons)} (the next search re-indexes)")


def _print_sources(sources: list[config.Source]) -> None:
    width = max((len(s.name) for s in sources), default=0)
    for source in sources:
        if source.type == config.GITLAB_ISSUES:
            where = source.url
            n = len(source.files())
            found = (
                f"{_plural(n, 'issue')}, mirrored in {config.tilde(source.path)}"
                if source.path.is_dir()
                else f"not fetched yet, run `ogmios fetch {source.name}`"
            )
        elif source.path.is_dir():
            where = f"{config.tilde(source.path)}/{source.glob}"
            found = (
                f"{_plural(len(source.files()), 'file')}, split: {source.options.split}"
            )
        else:
            where = config.tilde(source.path)
            state = "file" if source.path.is_file() else "missing"
            found = f"{state}, split: {source.options.split}"
        print(f"  {source.name:<{width}}  {source.type:<13}  {where}  ({found})")


def cmd_source_list(args: argparse.Namespace) -> None:
    sources = _config(args).sources
    if not sources:
        print("no sources, add one with `ogmios source add`")
    _print_sources(sources)


def cmd_source_add_local(args: argparse.Namespace) -> None:
    path = args.path.expanduser().absolute()
    if not path.exists():
        raise ValueError(f"{args.path}: no such file or directory")
    entry: dict = {}
    if args.name:
        entry["name"] = args.name
    entry |= {"type": config.LOCAL, "path": config.tilde(path)}
    if args.glob:
        entry["glob"] = args.glob
    if args.exclude:
        entry["exclude"] = args.exclude
    if args.split:
        entry["split"] = args.split
    if args.delimiter:
        for pattern in args.delimiter:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid delimiter {pattern!r}: {e}") from None
        entry["delimiters"] = args.delimiter
    if args.month_first:
        entry["day_first"] = False
    source = config.add_source(args.config, entry)
    found = _plural(len(source.files()), "file") if path.is_dir() else "file"
    print(
        f"added source {source.name}: {entry['path']} ({found}),"
        " indexed on the next search"
    )


def cmd_source_add_gitlab(args: argparse.Namespace) -> None:
    entry: dict = {}
    if args.name:
        entry["name"] = args.name
    entry |= {"type": config.GITLAB_ISSUES, "url": gitlab.project_url(args.url)}
    source = config.add_source(args.config, entry)
    print(
        f"added source {source.name}: the issues of {source.url},"
        f" run `ogmios fetch {source.name}` to mirror them"
    )


def cmd_source_remove(args: argparse.Namespace) -> None:
    source = config.remove_source(args.config, args.name)
    print(f"removed source {source.name}")
    if source.fetched and source.path.is_dir():
        shutil.rmtree(source.path)
        print(f"  deleted its mirror, {config.tilde(source.path)}")


def cmd_fetch(args: argparse.Namespace) -> None:
    cfg = config.load(args.config)
    fetched = [s for s in cfg.sources if s.fetched]
    if args.name:
        by_name = {s.name: s for s in cfg.sources}
        for name in args.name:
            if name not in by_name:
                raise ValueError(
                    f"no source named {name!r} (sources: {', '.join(by_name) or 'none'})"
                )
            if not by_name[name].fetched:
                raise ValueError(f"{name} is a local source, there is nothing to fetch")
        fetched = [by_name[n] for n in dict.fromkeys(args.name)]
    if not fetched:
        raise ValueError(
            "no source to fetch, add one with `ogmios source add gitlab-issues URL`"
        )
    tokens: dict[str, str | None] = {}
    interactive = sys.stderr.isatty()
    for source in fetched:
        assert source.url is not None
        host = gitlab.host(source.url)
        if host not in tokens:
            tokens[host] = _gitlab_token(host)
        with Spinner(enabled=interactive) as spinner:
            spinner.status(f"fetching {source.name}")
            stats = gitlab.fetch(
                source.url,
                source.path,
                tokens[host],
                full=args.full,
                progress=lambda n, name=source.name: spinner.status(
                    f"fetching {name}: {_plural(n, 'issue')}"
                ),
            )
        removed = f", {stats.removed} removed" if stats.removed else ""
        print(
            f"{source.name}: {_plural(stats.written, 'issue')} updated{removed},"
            " indexed on the next search"
        )


def _gitlab_token(host: str) -> str | None:
    """$GITLAB_TOKEN, else asked for; none for a public project."""
    if token := os.environ.get("GITLAB_TOKEN"):
        return token
    if not sys.stdin.isatty():
        return None
    token = getpass.getpass(f"GitLab token for {host} (empty for a public project): ")
    return token.strip() or None


def cmd_config_model(args: argparse.Namespace) -> None:
    if args.name is None:
        print(config.load(args.config).model)
        return
    config.set_value(args.config, "model", args.name)
    if Embedder(args.name, config.cache_dir() / "models").downloaded:
        print(f"model set to {args.name}, notes are re-embedded on the next search")
    else:
        print(f"model set to {args.name}, run `ogmios install`")


def cmd_config_llm(args: argparse.Namespace) -> None:
    if args.name is None:
        print(config.load(args.config).llm)
        return
    value = ask.llm_value(args.name)
    config.set_value(args.config, "llm", value)
    path = ask.model_path(ask.llm_spec(value), config.cache_dir() / "models")
    if path is not None and path.exists():
        print(f"llm set to {value}")
    else:
        print(f"llm set to {value}, run `ogmios install`")


def cmd_config_threads(args: argparse.Namespace) -> None:
    if args.number is not None:
        config.set_value(args.config, "threads", args.number)
    threads = config.load(args.config).threads
    print(threads if threads else "no cap")


def cmd_config_show(args: argparse.Namespace) -> None:
    if not args.config.exists():
        raise ValueError(
            f"no config file at {config.tilde(args.config)},"
            " add a source with `ogmios source add`"
        )
    print(args.config.read_text(encoding="utf-8"), end="")


def cmd_config_edit(args: argparse.Namespace) -> None:
    if not args.config.exists():
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_text(_TEMPLATE, encoding="utf-8")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    subprocess.run([*shlex.split(editor), str(args.config)], check=True)
    cfg = config.load(args.config)  # report mistakes right away
    print(f"{_plural(len(cfg.sources), 'source')}, model {cfg.model}")


_TEMPLATE = """\
# model = "e5-small-int8"   # or "e5-small" (fp32, slower, same results)
#                           # or "e5-base-int8" (larger, slower, see the README)
# llm = "gemma-4-e2b"       # or any GGUF file on Hugging Face: "REPO/FILE.gguf"
# threads = 4               # at most this many CPU threads (default: no cap)

# [[sources]]
# name = "done"                          # default: the file's stem
# type = "local"                         # the default
# path = "~/work/done.md"
# split = "auto"                         # auto | dates | headings
# delimiters = ['^## \\d{4}-\\d{2}-\\d{2}'] # if auto-detection gets it wrong
# day_first = true                       # 03/04/2024 is the 3rd of April

# [[sources]]
# path = "~/notes"
# glob = "**/*.md"
# exclude = ["archive/**"]

# [[sources]]
# type = "gitlab-issues"                 # mirrored by `ogmios fetch`
# url = "https://gitlab.example.com/group/project"
"""


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' * (n != 1)}"


def _size(path: Path) -> str:
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    return hub.human(sum(f.stat().st_size for f in files))


if __name__ == "__main__":
    main()
