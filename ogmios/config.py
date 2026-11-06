"""Configuration file and XDG locations.

~/.config/ogmios/config.toml:

    model = "e5-small-int8"          # optional
    llm = "gemma-4-e2b"              # optional, or any GGUF file: "REPO/FILE.gguf"
    threads = 4                      # optional: at most this many CPU threads

    [[sources]]
    name = "done"                    # optional: the file's stem, the directory's name
    type = "local"                   # optional: files read where they are
    path = "~/work/done.md"
    # split = "auto"                 # auto | dates | headings
    # delimiters = ['^## \\d{4}-']   # regexes selecting the date lines, if auto-detection fails
    # day_first = true               # 03/04/2024 is the 3rd of April

    [[sources]]
    path = "~/notes"
    glob = "**/*.md"
    exclude = ["archive/**"]

    [[sources]]
    type = "gitlab-issues"           # mirrored by `ogmios fetch`, one file per issue
    url = "https://gitlab.example.com/group/project"

`add_source`, `remove_source` and `set_value` edit the file as text, so that comments and
layout are kept, and check the result before writing it.
"""

import itertools
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import gitlab
from .ask import DEFAULT_LLM, llm_spec
from .embed import DEFAULT_MODEL, MODELS
from .parse import SplitOptions

LOCAL = "local"
GITLAB_ISSUES = "gitlab-issues"
TYPES = (LOCAL, GITLAB_ISSUES)
_NAME_RE = re.compile(r"[A-Za-z0-9][\w.-]*")


@dataclass
class Source:
    path: Path  # a file or a directory; for a fetched source, where it is mirrored
    glob: str = "**/*.md"
    exclude: list[str] = field(default_factory=list)
    options: SplitOptions = field(default_factory=SplitOptions)
    name: str = ""
    type: str = LOCAL
    url: str | None = None  # what a fetched source mirrors

    @property
    def fetched(self) -> bool:
        return self.type != LOCAL

    def files(self) -> list[Path]:
        if self.path.is_file():
            return [self.path]
        if not self.path.is_dir():
            return []
        return sorted(
            p
            for p in self.path.glob(self.glob)
            if p.is_file()
            and not any(
                p.relative_to(self.path).full_match(pattern) for pattern in self.exclude
            )
        )


@dataclass
class Config:
    sources: list[Source]
    model: str = DEFAULT_MODEL
    llm: str = DEFAULT_LLM
    threads: int | None = None  # at most this many CPU threads; None: no cap


def config_path() -> Path:
    if value := os.environ.get("OGMIOS_CONFIG"):
        return Path(value)
    return _xdg("XDG_CONFIG_HOME", ".config") / "ogmios" / "config.toml"


def db_path() -> Path:
    if value := os.environ.get("OGMIOS_DB"):
        return Path(value)
    return data_dir() / "index.sqlite"


def log_path() -> Path | None:
    """Where `ask` logs what it did; None when logging is off (`OGMIOS_LOG=off`)."""
    value = os.environ.get("OGMIOS_LOG")
    if value == "off":
        return None
    return Path(value) if value else data_dir() / "asks.jsonl"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "ogmios"


def gitlab_dir() -> Path:
    """Where the issues of `gitlab-issues` sources are mirrored."""
    return data_dir() / "gitlab-issues"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "ogmios"


def _xdg(variable: str, default: str) -> Path:
    value = os.environ.get(variable)
    return Path(value) if value else Path.home() / default


def load(path: Path) -> Config:
    cfg = _parse(_read(path))
    if (value := os.environ.get("OGMIOS_THREADS")) is not None:
        cfg.threads = _threads(value)
    return cfg


def _threads(value: object) -> int | None:
    """A cap on CPU threads, from the config or the environment; 0 is no cap."""
    try:
        threads = None if isinstance(value, bool) else int(str(value))
    except ValueError:
        threads = None
    if threads is None or threads < 0:
        raise ValueError(f"invalid threads {value!r}: a number, or 0 for no cap")
    return threads or None


def _parse(text: str) -> Config:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"invalid config: {e}") from None
    sources = [_source(entry) for entry in raw.get("sources", [])]
    seen: set[str] = set()
    for source in sources:
        if source.name in seen:
            raise ValueError(
                f"two sources named {source.name!r}: give one a `name` in the config"
            )
        seen.add(source.name)
    model = raw.get("model", DEFAULT_MODEL)
    if model not in MODELS:
        raise ValueError(
            f"unknown model {model!r}, expected one of: {', '.join(MODELS)}"
        )
    llm = raw.get("llm", DEFAULT_LLM)
    if not isinstance(llm, str):
        # A mistake in the file, reported as such by main(), not a bug: ValueError.
        raise ValueError("llm must be a string, see `ogmios config llm --help`")  # noqa: TRY004
    llm_spec(llm)
    threads = _threads(raw.get("threads", 0))
    return Config(sources=sources, model=model, llm=llm, threads=threads)


def _source(entry: dict) -> Source:
    kind = entry.get("type", LOCAL)
    if kind == GITLAB_ISSUES:
        if "url" not in entry:
            raise ValueError("a gitlab-issues source without a url")
        url = gitlab.project_url(entry["url"])
        return Source(
            path=gitlab.mirror_dir(gitlab_dir(), url),
            glob="*.md",
            options=gitlab.SPLIT,
            name=_name(entry.get("name") or gitlab.default_name(url)),
            type=kind,
            url=url,
        )
    if kind != LOCAL:
        raise ValueError(
            f"unknown source type {kind!r}, expected one of: {', '.join(TYPES)}"
        )
    if "path" not in entry:
        raise ValueError("a local source without a path")
    path = Path(entry["path"]).expanduser()
    options = SplitOptions(
        split=entry.get("split", "auto"),
        delimiters=[re.compile(p) for p in entry.get("delimiters", [])],
        day_first=entry.get("day_first", True),
    )
    if options.split not in ("auto", "dates", "headings"):
        raise ValueError(
            f"invalid split {options.split!r} for source {entry['path']!r}"
        )
    return Source(
        path=path,
        glob=entry.get("glob", "**/*.md"),
        exclude=entry.get("exclude", []),
        options=options,
        name=_name(entry.get("name") or default_name(path)),
    )


def default_name(path: Path) -> str:
    """A local source's name when the config gives none: "done" for ~/work/done.md."""
    return path.stem if path.suffix else path.name


def _name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid source name {name!r}: letters, digits, '.', '-' and '_' only"
        )
    return name


def add_source(config_file: Path, entry: dict) -> Source:
    """Append a `[[sources]]` table; `entry` holds its keys, in the order to write them."""
    text = _read(config_file)
    sources = _parse(text).sources
    added = _source(entry)
    for s in sources:
        if s.name == added.name:
            raise ValueError(
                f"there is already a source named {added.name!r}, choose another --name"
            )
        if (s.url or s.path.resolve()) == (added.url or added.path.resolve()):
            raise ValueError(
                f"{added.url or entry['path']} is already source {s.name!r}"
            )
    table = "[[sources]]\n" + "".join(f"{k} = {_toml(v)}\n" for k, v in entry.items())
    text = text.rstrip("\n") + "\n\n" + table if text.strip() else table
    return _write(config_file, text).sources[-1]


def remove_source(config_file: Path, name: str) -> Source:
    """Remove the source named `name`, and return it."""
    text = _read(config_file)
    sources = _parse(text).sources
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if _TABLE_RE.match(line)]
    tables = [
        (start, end)
        for start, end in zip(headers, [*headers[1:], len(lines)])
        if lines[start].split("#")[0].strip() == "[[sources]]"
    ]
    # The n-th [[sources]] table is the n-th source.
    for source, (start, end) in zip(sources, tables):
        if source.name != name:
            continue
        # Comment lines right above the next table describe that one: keep them.
        while end > start + 1 and lines[end - 1].lstrip().startswith("#"):
            end -= 1
        del lines[start:end]
        _write(config_file, re.sub(r"\n{3,}", "\n\n", "".join(lines)))
        return source
    names = ", ".join(s.name for s in sources) or "none"
    raise ValueError(f"no source named {name!r} (sources: {names})")


def set_value(config_file: Path, key: str, value: str | int) -> None:
    """Set a top-level key, such as `model`."""
    lines = _read(config_file).splitlines(keepends=True)
    line = f"{key} = {_toml(value)}\n"
    top_level = list(itertools.takewhile(lambda l: not _TABLE_RE.match(l), lines))
    keys = [i for i, l in enumerate(top_level) if re.match(r"\s*\w+\s*=", l)]
    same = [i for i in keys if re.match(rf"\s*{key}\s*=", top_level[i])]
    if same:
        lines[same[0]] = line
    elif keys:
        lines.insert(keys[-1] + 1, line)
    else:
        # After the comments that open the file, if any.
        i = 0
        while i < len(lines) and lines[i].lstrip().startswith("#"):
            i += 1
        lines[i:i] = [line, "\n"] if i < len(lines) else [line]
    _write(config_file, "".join(lines))


def tilde(path: Path) -> str:
    """`path`, absolute, with the home directory shown as ~."""
    path = path.expanduser().absolute()
    if path.is_relative_to(Path.home()):
        return str(Path("~") / path.relative_to(Path.home()))
    return str(path)


_TABLE_RE = re.compile(r"^\s*\[")


def _toml(value: str | bool | int | list[str]) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    quoted = json.dumps(value, ensure_ascii=False)
    # Literal strings keep regexes readable: no backslash escapes.
    if quoted != f'"{value}"' and "'" not in value and "\n" not in value:
        return f"'{value}'"
    return quoted


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write(path: Path, text: str) -> Config:
    config = _parse(text)  # never write a config that does not load
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)
    return config
