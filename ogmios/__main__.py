import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

from . import config, store
from .embed import MODELS, Embedder
from .parse import SplitOptions, chunk_section, parse_file
from .search import search


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
    parser = argparse.ArgumentParser(prog="ogmios", description="Search your notes.")
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
        help="file or directory to search, instead of the configured sources (repeatable)",
    )
    parser.add_argument("--model", choices=sorted(MODELS), help="embedding model")
    commands = parser.add_subparsers(required=True)

    index = commands.add_parser("index", help="(re)build the index")
    index.add_argument(
        "--force", action="store_true", help="rebuild even if nothing changed"
    )
    index.set_defaults(func=cmd_index)

    find = commands.add_parser(
        "search", help="search the notes (refreshes the index first)"
    )
    find.add_argument("query", nargs="+")
    find.add_argument("-n", "--limit", type=int, default=20)
    find.add_argument("--sort", choices=["score", "date"], default="score")
    find.add_argument("--json", action="store_true", help="one JSON object per line")
    find.add_argument(
        "--keyword", action="store_true", help="keyword search only (no model, instant)"
    )
    find.set_defaults(func=cmd_search)

    inspect = commands.add_parser("inspect", help="show how a file is split")
    inspect.add_argument("file", type=Path)
    inspect.add_argument("--chunks", action="store_true", help="also print every chunk")
    inspect.set_defaults(func=cmd_inspect)

    status = commands.add_parser(
        "status", help="show where files are stored and the state of the index"
    )
    status.set_defaults(func=cmd_status)

    return parser.parse_args()


def _setup(args: argparse.Namespace) -> tuple[config.Config, Embedder]:
    cfg = config.load(args.config)
    if args.source:
        cfg.sources = [config.Source(path=p.expanduser()) for p in args.source]
    if not cfg.sources:
        raise ValueError(
            f"no sources: pass --source or configure them in {args.config}"
        )
    model = args.model or cfg.model
    return cfg, Embedder(model, config.cache_dir() / "models")


def _refresh(
    args: argparse.Namespace,
    cfg: config.Config,
    embedder: Embedder,
    force: bool = False,
):
    db = store.connect(args.db)
    files = store.collect_files(cfg.sources)
    if force or store.is_stale(db, files, embedder.name):
        stats = store.rebuild(db, files, embedder)
        print(
            f"indexed {stats.files} files: {stats.sections} sections, {stats.chunks} chunks"
            f" ({stats.embedded} newly embedded)",
            file=sys.stderr,
        )
    return db


def cmd_index(args: argparse.Namespace) -> None:
    cfg, embedder = _setup(args)
    _refresh(args, cfg, embedder, force=args.force)


def cmd_search(args: argparse.Namespace) -> None:
    cfg, embedder = _setup(args)
    query = " ".join(args.query)
    if args.keyword:
        db = store.connect(args.db)
        hits = search(db, None, query, args.limit, args.sort)
    else:
        db = _refresh(args, cfg, embedder)
        hits = search(db, embedder, query, args.limit, args.sort)
    for hit in hits:
        if args.json:
            print(json.dumps(asdict(hit), ensure_ascii=False))
        else:
            # grep-like, so that any picker can open the location
            print(f"{hit.path}:{hit.line}:1:{hit.date or hit.title} │ {hit.snippet}")


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
    print(f"  config  {_home(args.config)}{config_note}{overridden('OGMIOS_CONFIG')}")
    db_note = f"  ({_size(args.db)})" if args.db.exists() else "  (not created yet)"
    print(f"  index   {_home(args.db)}{db_note}{overridden('OGMIOS_DB')}")
    print(f"  models  {_home(config.cache_dir() / 'models')}")

    print(f"\nmodel  {embedder.name}  ({embedder.spec.repo})")
    if embedder.downloaded:
        print(f"  {_home(embedder.dir)}  ({_size(embedder.dir)})")
    else:
        print("  not downloaded yet, run `ogmios index`")

    print(
        "\nsources" + ("" if cfg.sources else "  none, pass --source or configure them")
    )
    for source in cfg.sources:
        if source.path.is_dir():
            where = f"{_home(source.path)}/{source.glob}"
            found = _plural(len(source.files()), "file")
        else:
            where = str(_home(source.path))
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


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'s' * (n != 1)}"


def _home(path: Path) -> Path:
    """`path` with the home directory shown as ~."""
    path = path.expanduser().absolute()
    if path.is_relative_to(Path.home()):
        return Path("~") / path.relative_to(Path.home())
    return path


def _size(path: Path) -> str:
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    size = float(sum(f.stat().st_size for f in files))
    for unit in ("B", "kB", "MB"):
        if size < 1000:
            return f"{size:.0f} {unit}"
        size /= 1000
    return f"{size:.1f} GB"


if __name__ == "__main__":
    main()
