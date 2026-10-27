"""Split a markdown file into sections (days, or top-level headings), blocks and chunks.

A section is a daily entry in a dated file, or a top-level heading elsewhere. Inside a
section, a block is a top-level item and everything indented under it (a project and its
notes). Chunks are what gets indexed, at three overlapping granularities: the whole
section, each block, and each pair of adjacent blocks, so that a topic running across two
items or two projects is still embedded in one piece.
"""

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path

from .dates import (
    HEADING_RE,
    Delimiter,
    delimiters_from_patterns,
    detect_delimiters,
)

# Below this many delimiters a file is not considered a daily-notes file.
MIN_DELIMITERS = 3
# Longer blocks are split into overlapping windows of lines.
MAX_CHUNK_CHARS = 1500
WINDOW_OVERLAP_LINES = 2

_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s|>|\d+[.)]\s|#{1,6}\s)")
_STRIP_RE = re.compile(r"^[\s>#*+\-\d.)]*")
_HEADING_LEVEL_RE = re.compile(r"^ {0,3}(#{1,6})\s")
_RULE_RE = re.compile(r"^\s*([-*_=])(\s*\1){2,}\s*$")


def _blank(line: str) -> bool:
    """Blank lines and horizontal rules separate content but carry none."""
    return not line.strip() or bool(_RULE_RE.match(line))


@dataclass
class Block:
    start: int  # 0-based, inclusive
    end: int  # 0-based, exclusive
    title: str


@dataclass
class Section:
    start: int  # line of the header (or first line), 0-based
    end: int  # exclusive
    title: str
    date: str | None  # ISO date for daily entries
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Chunk:
    kind: str  # "section" | "block" | "pair" | "window"
    start: int
    end: int
    context: str
    text: str

    @property
    def embed_text(self) -> str:
        return f"{self.context}\n{self.text}" if self.context else self.text


@dataclass
class SplitOptions:
    split: str = "auto"  # "auto" | "dates" | "headings"
    delimiters: list[re.Pattern[str]] = field(default_factory=list)
    day_first: bool = True


def parse_file(path: Path, lines: list[str], options: SplitOptions) -> list[Section]:
    delimiters: list[Delimiter] = []
    if options.delimiters:
        delimiters = delimiters_from_patterns(
            lines, options.delimiters, options.day_first
        )
    elif options.split in ("auto", "dates"):
        delimiters = detect_delimiters(lines, options.day_first)
        if options.split == "auto" and len(delimiters) < MIN_DELIMITERS:
            delimiters = []

    if delimiters:
        sections = _dated_sections(path, lines, delimiters)
    else:
        sections = _heading_sections(path, lines)

    for section in sections:
        body_start = section.start + 1 if _is_header(section, lines) else section.start
        section.blocks = split_blocks(lines, body_start, section.end)
    return [s for s in sections if any(lines[i].strip() for i in range(s.start, s.end))]


def _is_header(section: Section, lines: list[str]) -> bool:
    return section.date is not None or bool(HEADING_RE.match(lines[section.start]))


def _dated_sections(
    path: Path, lines: list[str], delimiters: list[Delimiter]
) -> list[Section]:
    sections = []
    if delimiters[0].line > 0:
        sections.append(Section(0, delimiters[0].line, path.stem, None))
    for d, nxt in zip(delimiters, delimiters[1:] + [None]):
        end = nxt.line if nxt else len(lines)
        found = d.date
        if found is not None:
            iso = found.isoformat()
            title = f"{iso} {d.label}" if d.label else iso
            sections.append(Section(d.line, end, title, iso))
        else:
            sections.append(
                Section(d.line, end, _clean(lines[d.line]) or path.stem, None)
            )
    return sections


def _heading_sections(path: Path, lines: list[str]) -> list[Section]:
    """Split on the shallowest heading level used at least twice; else one section."""
    levels: dict[int, list[int]] = {}
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and (m := _HEADING_LEVEL_RE.match(line)):
            levels.setdefault(len(m.group(1)), []).append(i)

    starts = next((levels[lvl] for lvl in sorted(levels) if len(levels[lvl]) >= 2), [])
    if not starts:
        return [Section(0, len(lines), path.stem, None)]

    sections = []
    if starts[0] > 0:
        first = next((line for line in lines[: starts[0]] if line.strip()), "")
        title = (
            f"{path.stem} › {_clean(first)}" if HEADING_RE.match(first) else path.stem
        )
        sections.append(Section(0, starts[0], title, None))
    for start, nxt in zip(starts, starts[1:] + [len(lines)]):
        sections.append(
            Section(start, nxt, f"{path.stem} › {_clean(lines[start])}", None)
        )
    return sections


def _quote_depth(text: str) -> int:
    prefix = text[: len(text) - len(text.lstrip("> "))]
    return prefix.count(">")


def _indent(line: str) -> int:
    """Indentation width, counting each nested '>' quote level as one more level."""
    expanded = line.expandtabs(4)
    width = len(expanded) - len(expanded.lstrip(" "))
    quotes = _quote_depth(expanded[width:])
    return width + 2 * max(quotes - 1, 0)


def split_blocks(lines: list[str], start: int, end: int) -> list[Block]:
    body = [i for i in range(start, end) if not _blank(lines[i])]
    if not body:
        return []
    base = min(_indent(lines[i]) for i in body)

    blocks: list[Block] = []
    current: int | None = None
    previous_blank = False
    in_fence = False
    for i in range(start, end):
        line = lines[i]
        if _blank(line):
            previous_blank = True
            continue
        fence = line.lstrip().startswith("```")
        starts_block = not in_fence and (
            current is None
            or bool(HEADING_RE.match(line))
            or (
                _indent(line) <= base
                and (bool(_MARKER_RE.match(line)) or previous_blank)
            )
        )
        if fence:
            in_fence = not in_fence
        if starts_block:
            if current is not None:
                blocks.append(
                    Block(current, _trim_end(lines, current, i), _clean(lines[current]))
                )
            current = i
        previous_blank = False
    if current is not None:
        blocks.append(
            Block(current, _trim_end(lines, current, end), _clean(lines[current]))
        )
    return blocks


def _trim_end(lines: list[str], start: int, end: int) -> int:
    while end > start and _blank(lines[end - 1]):
        end -= 1
    return end


def _clean(line: str, limit: int = 60) -> str:
    text = _STRIP_RE.sub("", line).strip().strip("*_:").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start:end]).strip()


def _windows(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Cut [start, end) into overlapping line windows of at most MAX_CHUNK_CHARS."""
    windows = []
    i = start
    while i < end:
        j, size = i, 0
        while j < end and (j == i or size + len(lines[j]) + 1 <= MAX_CHUNK_CHARS):
            size += len(lines[j]) + 1
            j += 1
        windows.append((i, j))
        if j >= end:
            break
        i = max(j - WINDOW_OVERLAP_LINES, i + 1)
    return windows


def chunk_section(lines: list[str], section: Section) -> list[Chunk]:
    chunks = []
    whole = _text(lines, section.start, section.end)
    if len(whole) <= MAX_CHUNK_CHARS or not section.blocks:
        chunks.append(
            Chunk("section", section.start, section.end, section.title, whole)
        )
    else:
        # Too long to embed in one piece: the head still gives a day-level view.
        head_end = _windows(lines, section.start, section.end)[0][1]
        chunks.append(
            Chunk(
                "section",
                section.start,
                head_end,
                section.title,
                _text(lines, section.start, head_end),
            )
        )

    blocks = section.blocks
    if len(blocks) <= 1 and len(whole) <= MAX_CHUNK_CHARS:
        return chunks

    for block in blocks:
        windows = _windows(lines, block.start, block.end)
        if len(windows) == 1:
            chunks.append(
                Chunk(
                    "block",
                    block.start,
                    block.end,
                    section.title,
                    _text(lines, block.start, block.end),
                )
            )
            continue
        for k, (ws, we) in enumerate(windows):
            # Later windows lose the block's title line, so carry it in the context.
            context = section.title if k == 0 else f"{section.title} · {block.title}"
            chunks.append(Chunk("window", ws, we, context, _text(lines, ws, we)))

    if len(blocks) >= 3:
        for a, b in itertools.pairwise(blocks):
            text = _text(lines, a.start, b.end)
            if len(text) <= MAX_CHUNK_CHARS:
                chunks.append(Chunk("pair", a.start, b.end, section.title, text))
    return chunks
