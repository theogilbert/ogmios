"""Configuration file and XDG locations.

~/.config/ogmios/config.toml:

    model = "e5-small-int8"          # optional
    llm = "gemma-4-e2b"              # optional, or any GGUF file: "REPO/FILE.gguf"

    [[sources]]
    path = "~/work/done.md"
    # split = "auto"                 # auto | dates | headings
    # delimiters = ['^## \\d{4}-']   # regexes selecting the date lines, if auto-detection fails
    # day_first = true               # 03/04/2024 is the 3rd of April

    [[sources]]
    path = "~/notes"
    glob = "**/*.md"
    exclude = ["archive/**"]

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

from .ask import DEFAULT_LLM, llm_spec
from .embed import DEFAULT_MODEL, MODELS
from .parse import SplitOptions


@dataclass
class Source:
    path: Path
    glob: str = "**/*.md"
    exclude: list[str] = field(default_factory=list)
    options: SplitOptions = field(default_factory=SplitOptions)

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


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "ogmios"


def _xdg(variable: str, default: str) -> Path:
    value = os.environ.get(variable)
    return Path(value) if value else Path.home() / default


def load(path: Path) -> Config:
    return _parse(_read(path))


def _parse(text: str) -> Config:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"invalid config: {e}") from None
    sources = [_source(entry) for entry in raw.get("sources", [])]
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
    return Config(sources=sources, model=model, llm=llm)


def _source(entry: dict) -> Source:
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
        path=Path(entry["path"]).expanduser(),
        glob=entry.get("glob", "**/*.md"),
        exclude=entry.get("exclude", []),
        options=options,
    )


def add_source(config_file: Path, entry: dict) -> Source:
    """Append a `[[sources]]` table; `entry` holds its keys, `path` first."""
    text = _read(config_file)
    target = Path(entry["path"]).expanduser().resolve()
    if any(s.path.resolve() == target for s in _parse(text).sources):
        raise ValueError(f"{entry['path']} is already a source")
    table = "[[sources]]\n" + "".join(f"{k} = {_toml(v)}\n" for k, v in entry.items())
    text = text.rstrip("\n") + "\n\n" + table if text.strip() else table
    return _write(config_file, text).sources[-1]


def remove_source(config_file: Path, path: Path) -> None:
    lines = _read(config_file).splitlines(keepends=True)
    target = path.expanduser().resolve()
    headers = [i for i, line in enumerate(lines) if _TABLE_RE.match(line)]
    for start, end in zip(headers, [*headers[1:], len(lines)]):
        if lines[start].split("#")[0].strip() != "[[sources]]":
            continue
        entry = tomllib.loads("".join(lines[start + 1 : end]))
        if Path(entry.get("path", "")).expanduser().resolve() != target:
            continue
        # Comment lines right above the next table describe that one: keep them.
        while end > start + 1 and lines[end - 1].lstrip().startswith("#"):
            end -= 1
        del lines[start:end]
        _write(config_file, re.sub(r"\n{3,}", "\n\n", "".join(lines)))
        return
    raise ValueError(f"{path} is not a configured source")


def set_value(config_file: Path, key: str, value: str) -> None:
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


def _toml(value: str | bool | list[str]) -> str:
    if isinstance(value, bool):
        return str(value).lower()
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
