"""Detection of date delimiter lines (daily-note headers) in English and French."""

import itertools
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "january": 1, "janvier": 1, "janv": 1, "jan": 1,
    "february": 2, "février": 2, "fevrier": 2, "févr": 2, "fevr": 2, "fév": 2, "fev": 2, "feb": 2,
    "march": 3, "mars": 3, "mar": 3,
    "april": 4, "avril": 4, "avr": 4, "apr": 4,
    "may": 5, "mai": 5,
    "june": 6, "juin": 6, "jun": 6,
    "july": 7, "juillet": 7, "juil": 7, "jul": 7,
    "august": 8, "août": 8, "aout": 8, "aug": 8,
    "september": 9, "septembre": 9, "sept": 9, "sep": 9,
    "october": 10, "octobre": 10, "oct": 10,
    "november": 11, "novembre": 11, "nov": 11,
    "december": 12, "décembre": 12, "decembre": 12, "déc": 12, "dec": 12,
}  # fmt: skip

WEEKDAYS = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
    "mon", "tues", "tue", "wed", "thurs", "thur", "thu", "fri", "sat", "sun",
    "lun", "mar", "mer", "jeu", "ven", "sam", "dim",
]  # fmt: skip

_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_SUFFIX = r"(?:er|st|nd|rd|th)?"

# Each pattern yields named groups among y/m/d/mon. Order matters: first match wins.
_PATTERNS = [
    re.compile(r"(?<!\d)(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})(?!\d)"),
    re.compile(
        r"(?<!\d)(?P<d>\d{1,2})[-/.](?P<m>\d{1,2})[-/.](?P<y>\d{4}|\d{2})(?!\d)"
    ),
    re.compile(
        rf"(?<!\d)(?P<d>\d{{1,2}}){_SUFFIX}\.?\s+(?P<mon>{_MON})\b\.?(?:,?\s+(?P<y>\d{{4}}))?(?!\d)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?P<mon>{_MON})\b\.?\s+(?P<d>\d{{1,2}}){_SUFFIX}(?:,?\s+(?P<y>\d{{4}}))?(?!\d)",
        re.IGNORECASE,
    ),
    # Day/month without a year is only trusted with a slash ("12/03"), "1.5" is too common.
    re.compile(r"(?<![\d/])(?P<d>\d{1,2})/(?P<m>\d{1,2})(?![\d/])"),
]

_WEEKDAY_RE = re.compile(rf"\b(?:{'|'.join(WEEKDAYS)})\b\.?", re.IGNORECASE)
_DECORATION_RE = re.compile(r"[\W_]+")
HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s")

MAX_HEADER_LEN = 80
# How many non-date characters a delimiter line may hold ("## 2024-05-14 - Sprint review").
MAX_HEADING_LEFTOVER = 40
MAX_PLAIN_LEFTOVER = 10


@dataclass
class DateMatch:
    start: int
    end: int
    year: int | None
    month: int
    day: int


@dataclass
class Delimiter:
    line: int  # 0-based index into the file's lines
    year: int | None
    month: int | None
    day: int | None
    heading: bool
    label: str = ""  # what the header says besides the date ("Reprise")

    @property
    def date(self) -> date | None:
        if self.year is None or self.month is None or self.day is None:
            return None
        return _safe_date(self.year, self.month, self.day)


def find_date(text: str, day_first: bool = True) -> DateMatch | None:
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            groups = m.groupdict()
            if groups.get("mon"):
                month = MONTHS[groups["mon"].lower()]
            else:
                month = int(groups["m"])
            day = int(groups["d"])
            if (
                "mon" not in pattern.groupindex
                and pattern is not _PATTERNS[0]
                and not day_first
            ):
                day, month = month, day
            year = int(groups["y"]) if groups.get("y") else None
            if year is not None and year < 100:
                year += 2000
            if not 1 <= month <= 12 or not 1 <= day <= 31:
                continue
            if year is not None and _safe_date(year, month, day) is None:
                continue
            return DateMatch(m.start(), m.end(), year, month, day)
    return None


def find_dates(text: str) -> list[DateMatch]:
    """Every date in the text, in order."""
    found = []
    while (m := find_date(text)) is not None:
        found.append(m)
        # Hidden, to look for the next one; positions stay the same.
        text = text[: m.start] + " " * (m.end - m.start) + text[m.end :]
    return sorted(found, key=lambda m: m.start)


def delimiter_candidate(
    index: int, line: str, day_first: bool = True
) -> Delimiter | None:
    """Return a delimiter if the line looks like a standalone date header."""
    if len(line) > MAX_HEADER_LEN:
        return None
    match = find_date(line, day_first)
    if match is None:
        return None
    heading = bool(HEADING_RE.match(line))
    label = _label(line, match)
    if len(_DECORATION_RE.sub("", label)) > (
        MAX_HEADING_LEFTOVER if heading else MAX_PLAIN_LEFTOVER
    ):
        return None
    return Delimiter(index, match.year, match.month, match.day, heading, label)


def _label(line: str, match: DateMatch) -> str:
    rest = _WEEKDAY_RE.sub(" ", line[: match.start] + " " + line[match.end :])
    words = [w for w in (_DECORATION_RE.sub(" ", w).strip() for w in rest.split()) if w]
    return " ".join(words)


def detect_delimiters(lines: list[str], day_first: bool = True) -> list[Delimiter]:
    candidates = [
        c
        for i, line in enumerate(lines)
        if (c := delimiter_candidate(i, line, day_first)) is not None
    ]
    if not candidates:
        return []
    descending = _is_descending(candidates)
    _infer_years(candidates, descending)
    return _drop_out_of_order(candidates, descending)


def delimiters_from_patterns(
    lines: list[str], patterns: list[re.Pattern[str]], day_first: bool = True
) -> list[Delimiter]:
    """Delimiters chosen by user-configured regexes; the date is still parsed from the line."""
    result = []
    for i, line in enumerate(lines):
        if any(p.search(line) for p in patterns):
            match = find_date(line, day_first)
            heading = bool(HEADING_RE.match(line))
            if match is None:
                result.append(Delimiter(i, None, None, None, heading))
            else:
                result.append(
                    Delimiter(
                        i,
                        match.year,
                        match.month,
                        match.day,
                        heading,
                        _label(line, match),
                    )
                )
    if result:
        _infer_years(result, _is_descending(result))
    return result


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _is_descending(candidates: list[Delimiter]) -> bool:
    dated = [d for c in candidates if (d := c.date) is not None]
    pairs = list(itertools.pairwise(dated))
    return sum(a > b for a, b in pairs) > sum(a < b for a, b in pairs)


def _infer_years(candidates: list[Delimiter], descending: bool) -> None:
    """Fill missing years from the nearest dated neighbour, assuming chronological order."""
    for order in (candidates, list(reversed(candidates))):
        # Walking backwards through a file flips which way time flows.
        flows_backwards = descending == (order is candidates)
        previous: date | None = None
        for c in order:
            if c.month is None or c.day is None:
                continue
            if c.year is not None:
                previous = c.date
                continue
            if previous is None:
                continue
            options = [
                d
                for y in (previous.year - 1, previous.year, previous.year + 1)
                if (d := _safe_date(y, c.month, c.day)) is not None
            ]
            consistent = [
                d
                for d in options
                if (d <= previous if flows_backwards else d >= previous)
            ]
            best = min(consistent or options, key=lambda d: abs((d - previous).days))
            c.year = best.year
            previous = best


def _drop_out_of_order(
    candidates: list[Delimiter], descending: bool
) -> list[Delimiter]:
    """Drop plain-line candidates that sit outside the order their neighbours agree on.

    Markdown headings are always trusted: they are an explicit delimiter.
    """

    def in_order(a: date, b: date) -> bool:
        return a >= b if descending else a <= b

    kept = []
    for i, c in enumerate(candidates):
        current = c.date
        if not c.heading and current is not None:
            prev = next((d for p in reversed(kept) if (d := p.date) is not None), None)
            nxt = next(
                (d for n in candidates[i + 1 :] if (d := n.date) is not None), None
            )
            if (
                prev is not None
                and nxt is not None
                and in_order(prev, nxt)
                and not (in_order(prev, current) and in_order(current, nxt))
            ):
                continue
        kept.append(c)
    return kept
