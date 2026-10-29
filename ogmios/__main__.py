import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path

from . import ask, config, hub, store
from .embed import MODELS, Embedder
from .parse import SplitOptions, chunk_section, parse_file
from .search import is_about_anything, search
from .spinner import Spinner
from .when import find_period


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
        "-s",
        "--source",
        action="append",
        type=Path,
        help="file or directory of notes, instead of the configured sources (repeatable)",
    )
    parser.add_argument("--model", choices=sorted(MODELS), help="embedding model")
    commands = parser.add_subparsers(required=True)

    setup = commands.add_parser(
        "setup",
        help="download the models and configure ogmios to use them",
        description="Download the embedding model (--model, else the configured one)"
        " and the LLM, then write both to the config file.",
    )
    setup.set_defaults(func=cmd_setup)

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
        "-n", "--days", type=int, default=8, help="most days of notes to read"
    )
    question.add_argument(
        "--prompt",
        action="store_true",
        help="print what the model would be given, without running it",
    )
    question.add_argument(
        "--json",
        action="store_true",
        help="print the answer and its sources as one JSON object, once written",
    )
    question.set_defaults(func=cmd_ask)

    inspect = commands.add_parser("inspect", help="show how a file is split")
    inspect.add_argument("file", type=Path)
    inspect.add_argument("--chunks", action="store_true", help="also print every chunk")
    inspect.set_defaults(func=cmd_inspect)

    status = commands.add_parser(
        "status", help="show where files are stored and the state of the index"
    )
    status.set_defaults(func=cmd_status)

    cfg = commands.add_parser("config", help="change the configuration")
    cfg_commands = cfg.add_subparsers(required=True)
    add = cfg_commands.add_parser("add", help="add a file or directory of notes")
    add.add_argument("path", type=Path)
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
    add.set_defaults(func=cmd_config_add)
    remove = cfg_commands.add_parser("remove", help="stop indexing a source")
    remove.add_argument("path", type=Path)
    remove.set_defaults(func=cmd_config_remove)
    model = cfg_commands.add_parser("model", help="show or set the embedding model")
    model.add_argument("name", nargs="?", choices=sorted(MODELS))
    model.set_defaults(func=cmd_config_model)
    show = cfg_commands.add_parser("show", help="print the config file")
    show.set_defaults(func=cmd_config_show)
    edit = cfg_commands.add_parser("edit", help="open the config file in $EDITOR")
    edit.set_defaults(func=cmd_config_edit)

    return parser.parse_args()


def _setup(args: argparse.Namespace) -> tuple[config.Config, Embedder]:
    cfg = config.load(args.config)
    if args.source:
        cfg.sources = [config.Source(path=p.expanduser()) for p in args.source]
    if not cfg.sources:
        raise ValueError(
            f"no sources: pass --source or configure them in {args.config}"
        )
    embedder = Embedder(args.model or cfg.model, config.cache_dir() / "models")
    # Before the index is touched, rather than halfway through re-embedding it.
    if not embedder.downloaded:
        raise ValueError(
            f"embedding model {embedder.name} is not installed, run `ogmios setup`"
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


def cmd_setup(args: argparse.Namespace) -> None:
    cfg = config.load(args.config)
    models = config.cache_dir() / "models"
    embedder = Embedder(args.model or cfg.model, models)
    spec, llm = embedder.spec, ask.LLMS[cfg.llm]
    wanted = [
        (
            f"embedding model {embedder.name}",
            spec.repo,
            spec.revision,
            embedder.dir,
            embedder.files,
        ),
        (
            f"LLM {cfg.llm}",
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
    config.set_value(args.config, "llm", cfg.llm)
    print(
        f'\nset model = "{embedder.name}", llm = "{cfg.llm}"'
        f" in {config.tilde(args.config)}"
    )


def cmd_index(args: argparse.Namespace) -> None:
    cfg, embedder = _setup(args)
    _refresh(args, cfg, embedder, force=args.force)


def cmd_ask(args: argparse.Namespace) -> None:
    cfg, embedder = _setup(args)
    model = ask.model_path(cfg.llm, config.cache_dir() / "models")
    if not args.prompt:
        hub.installed(model, f"LLM {cfg.llm}")
    question = " ".join(args.question)
    # Not over --json or a pipe: whatever reads stderr there wants lines, not frames.
    interactive = sys.stderr.isatty() and not (args.json or args.prompt)
    with Spinner(enabled=interactive) as spinner:
        _ask(args, cfg, embedder, model, question, spinner)


def _ask(
    args: argparse.Namespace,
    cfg: config.Config,
    embedder: Embedder,
    model: Path,
    question: str,
    spinner: Spinner,
) -> None:
    db = _refresh(args, cfg, embedder, spinner=spinner)
    spinner.status("searching the notes")
    period = None
    if found := find_period(question, date.today()):
        period, topic = found
        if is_about_anything(topic):
            excerpts = ask.excerpts_in(db, period)[: args.days]
        else:
            excerpts = ask.excerpts_for(
                db, search(db, embedder, topic, args.days, period=period)
            )
    else:
        excerpts = ask.excerpts_for(db, search(db, embedder, question, args.days))
    if not excerpts:
        where = f" from {period} ({period.words})" if period else ""
        raise ValueError(f"no notes{where} to answer from")
    chat = ask.messages(question, excerpts, date.today())
    if args.prompt:
        for message in chat:
            print(f"--- {message['role']}\n{message['content']}")
        return

    dates = sorted(e.date for e in excerpts if e.date)
    span = f", {dates[0]} to {dates[-1]}" if dates else ""
    reading = f"reading {_plural(len(excerpts), 'excerpt')}{span}"
    if not spinner.enabled:
        print(f"ogmios: {reading}", file=sys.stderr)
    spinner.status("loading the model")
    llm = ask.load(model)
    spinner.status(reading)
    if args.json:
        text = "".join(ask.answer(llm, chat)).strip()
        print(json.dumps(ask.result(question, text, excerpts), ensure_ascii=False))
        return
    written = []
    for piece in ask.answer(llm, chat):
        if not written:
            spinner.stop()
        written.append(piece)
        print(piece, end="", flush=True)
    print()
    sources = ask.cited("".join(written), excerpts)
    if sources:
        print()
    for i, e in enumerate(excerpts, 1):
        if e in sources:
            print(f"[{i}] {e.date or e.title} | {e.path}:{e.line}")


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
    cfg = config.load(args.config)
    if args.source:
        cfg.sources = [config.Source(path=p.expanduser()) for p in args.source]
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

    print(f"\nmodel  {embedder.name}  ({embedder.spec.repo})")
    if embedder.downloaded:
        print(f"  {config.tilde(embedder.dir)}  ({_size(embedder.dir)})")
    else:
        print("  not installed, run `ogmios setup`")
    llm = ask.model_path(cfg.llm, config.cache_dir() / "models")
    print(f"\nllm  {cfg.llm}  ({ask.LLMS[cfg.llm].repo})")
    if not llm.exists():
        print("  not installed, run `ogmios setup`")
    else:
        print(f"  {config.tilde(llm)}  ({_size(llm)})")

    print(
        "\nsources" + ("" if cfg.sources else "  none, pass --source or configure them")
    )
    for source in cfg.sources:
        if source.path.is_dir():
            where = f"{config.tilde(source.path)}/{source.glob}"
            found = _plural(len(source.files()), "file")
        else:
            where = config.tilde(source.path)
            found = "file" if source.path.is_file() else "missing"
        split = source.options.split
        print(f"  {where}  ({found}, split: {split})")

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


def cmd_config_add(args: argparse.Namespace) -> None:
    path = args.path.expanduser().absolute()
    if not path.exists():
        raise ValueError(f"{args.path}: no such file or directory")
    entry: dict = {"path": config.tilde(path)}
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
    print(f"added {entry['path']} ({found}), indexed on the next search")


def cmd_config_remove(args: argparse.Namespace) -> None:
    config.remove_source(args.config, args.path)
    print(f"removed {config.tilde(args.path)}")


def cmd_config_model(args: argparse.Namespace) -> None:
    if args.name is None:
        print(config.load(args.config).model)
        return
    config.set_value(args.config, "model", args.name)
    if Embedder(args.name, config.cache_dir() / "models").downloaded:
        print(f"model set to {args.name}, notes are re-embedded on the next search")
    else:
        print(f"model set to {args.name}, install it with `ogmios setup`")


def cmd_config_show(args: argparse.Namespace) -> None:
    if not args.config.exists():
        raise ValueError(
            f"no config file at {config.tilde(args.config)},"
            " add a source with `ogmios config add PATH`"
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
# llm = "gemma-4-e2b"       # for `ogmios ask`

# [[sources]]
# path = "~/work/done.md"
# split = "auto"                         # auto | dates | headings
# delimiters = ['^## \\d{4}-\\d{2}-\\d{2}'] # if auto-detection gets it wrong
# day_first = true                       # 03/04/2024 is the 3rd of April

# [[sources]]
# path = "~/notes"
# glob = "**/*.md"
# exclude = ["archive/**"]
"""


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' * (n != 1)}"


def _size(path: Path) -> str:
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    return hub.human(sum(f.stat().st_size for f in files))


if __name__ == "__main__":
    main()
