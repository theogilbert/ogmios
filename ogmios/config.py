"""Configuration file and XDG locations.

~/.config/ogmios/config.toml:

    model = "e5-small-int8"          # optional

    [[sources]]
    path = "~/work/done.md"
    # split = "auto"                 # auto | dates | headings
    # delimiters = ['^## \\d{4}-']   # regexes selecting the date lines, if auto-detection fails
    # day_first = true               # 03/04/2024 is the 3rd of April

    [[sources]]
    path = "~/notes"
    glob = "**/*.md"
    exclude = ["archive/**"]
"""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .embed import DEFAULT_MODEL
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


def config_path() -> Path:
    if value := os.environ.get("OGMIOS_CONFIG"):
        return Path(value)
    return _xdg("XDG_CONFIG_HOME", ".config") / "ogmios" / "config.toml"


def db_path() -> Path:
    if value := os.environ.get("OGMIOS_DB"):
        return Path(value)
    return data_dir() / "index.sqlite"


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "ogmios"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "ogmios"


def _xdg(variable: str, default: str) -> Path:
    value = os.environ.get(variable)
    return Path(value) if value else Path.home() / default


def load(path: Path) -> Config:
    if not path.exists():
        return Config(sources=[])
    with path.open("rb") as f:
        raw = tomllib.load(f)
    sources = [_source(entry) for entry in raw.get("sources", [])]
    return Config(sources=sources, model=raw.get("model", DEFAULT_MODEL))


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
