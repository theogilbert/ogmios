"""Time expressions in queries ("yesterday", "la semaine dernière", "in March 2024",
"between March and May 2024", "depuis septembre", "Q1 2024", "l'été dernier").

Embeddings know nothing of today's date, so "what did I do yesterday" would otherwise
match any day that reads like an answer. The expression is turned into a date range that
filters the search, and taken out of the query.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .dates import MONTHS, DateMatch, find_date

_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "un": 1, "une": 1, "two": 2, "deux": 2,
    "three": 3, "trois": 3, "four": 4, "quatre": 4, "five": 5, "cinq": 5,
}  # fmt: skip
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6, "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}  # fmt: skip
# French and English unit names, singular and plural, to "day" | "week" | "month" | "year".
_UNITS = {
    "day": "day", "days": "day", "jour": "day", "jours": "day",
    "week": "week", "weeks": "week", "semaine": "week", "semaines": "week",
    "month": "month", "months": "month", "mois": "month",
    "quarter": "quarter", "quarters": "quarter", "trimestre": "quarter",
    "trimestres": "quarter",
    "year": "year", "years": "year", "an": "year", "ans": "year", "année": "year",
    "années": "year", "annee": "year", "annees": "year",
}  # fmt: skip
# Meteorological seasons, by their first month: winter YEAR starts in December of YEAR.
_SEASONS = {
    "spring": 3, "printemps": 3, "summer": 6, "été": 6, "ete": 6,
    "autumn": 9, "fall": 9, "automne": 9, "winter": 12, "hiver": 12,
}  # fmt: skip
_QUARTERS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4,
    "4th": 4, "premier": 1, "1er": 1, "deuxième": 2, "deuxieme": 2, "2e": 2, "2ème": 2,
    "troisième": 3, "troisieme": 3, "3e": 3, "3ème": 3, "quatrième": 4, "quatrieme": 4,
    "4e": 4, "4ème": 4,
}  # fmt: skip

_N = rf"(?P<n>\d+|{'|'.join(_NUMBERS)})"
_UNIT = rf"(?P<unit>{'|'.join(sorted(_UNITS, key=len, reverse=True))})"
_DAY = rf"(?P<weekday>{'|'.join(_WEEKDAYS)})"
_MON = rf"(?P<mon>{'|'.join(sorted(MONTHS, key=len, reverse=True))})"
_SEASON = rf"(?P<season>{'|'.join(_SEASONS)})"
_QUARTER = rf"(?P<qw>{'|'.join(sorted(_QUARTERS, key=len, reverse=True))})"
_APOS = "['’]"
_LAST_FR = r"derni[eè]re?"


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # inclusive
    words: str  # how the query named it

    def __str__(self) -> str:
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()} to {self.end.isoformat()}"


def find_period(query: str, today: date) -> tuple[Period, str] | None:
    """The period a query refers to, and the query without the words naming it."""
    # Expressions built on another first, lest a part of them be taken for the whole:
    # "between March and May 2024" is not May.
    for composite in (_range, _since, _week_of, _offset):
        if found := composite(query, today):
            return found
    found = _match(_RELATIVE, query, today) or _match(_NAMED, query, today)
    return found or _day(query, today) or _match(_CALENDAR, query, today)


def _match(rules: list, query: str, today: date) -> tuple[Period, str] | None:
    for pattern, resolve in rules:
        if m := pattern.search(query):
            start, end = resolve(m.groupdict(), today)
            return Period(start, end, m.group(0)), _without(query, m.start(), m.end())
    return None


def _day(query: str, today: date) -> tuple[Period, str] | None:
    """An explicit date: "2024-03-14", "14 mars", "March 14, 2024"."""
    m = find_date(query)
    if m is None or (day := _resolve_day(m, today)) is None:
        return None
    return Period(day, day, query[m.start : m.end]), _without(query, m.start, m.end)


def _resolve_day(m: DateMatch, today: date) -> date | None:
    year = m.year
    if year is None:  # the latest such day, up to today
        year = today.year - ((m.month, m.day) > (today.month, today.day))
    try:
        return date(year, m.month, m.day)
    except ValueError:  # February 29th
        return None


def _date_at(query: str, pos: int) -> DateMatch | None:
    """The date written at `pos`, if any."""
    masked = query
    while (m := find_date(masked)) is not None:
        if m.start == pos:
            return m
        # Found elsewhere: hidden, to look for the next one.
        masked = masked[: m.start] + " " * (m.end - m.start) + masked[m.end :]
    return None


def _point(
    query: str, pos: int, today: date, ref: date | None = None
) -> tuple[date, date, int] | None:
    """The period named at `pos` (any expression, and also a bare month or year), and
    where its words end. Calendar expressions without a year are resolved as the latest
    up to `ref`, today by default."""
    ref = ref or today
    if (m := _date_at(query, pos)) and (day := _resolve_day(m, ref)):
        return day, day, m.end
    for rules, base in (
        (_RELATIVE, today),
        (_NAMED, ref),
        (_CALENDAR, ref),
        (_BARE, ref),
    ):
        for pattern, resolve in rules:
            if m := pattern.match(query, pos):
                start, end = resolve(m.groupdict(), base)
                return start, end, m.end()
    return None


_RANGE_FROM = re.compile(
    rf"(?<![\w-])(?:(?:between|from|entre|du|de)\s+|d{_APOS})(?:the\s+|le\s+)?",
    re.IGNORECASE,
)
_RANGE_TO = re.compile(
    rf"\s+(?:and|to|until|till|through|et|au|à|jusqu{_APOS}(?:au|à))\s+(?:the\s+|le\s+)?",
    re.IGNORECASE,
)


def _range(query: str, today: date) -> tuple[Period, str] | None:
    """ "between March and May 2024", "entre le 3 et le 10 avril", "from X to Y"."""
    for opening in _RANGE_FROM.finditer(query):
        limit = min(len(query), opening.end() + 40)
        for to in _RANGE_TO.finditer(query, opening.end(), limit):
            last = _point(query, to.end(), today)
            if last is None:
                continue
            first = _range_start(query[opening.end() : to.start()], last[1], today)
            if first is not None and first <= last[1]:
                period = Period(first, last[1], query[opening.start() : last[2]])
                return period, _without(query, opening.start(), last[2])
    return None


def _range_start(text: str, end: date, today: date) -> date | None:
    """Where a range starts; what it leaves out (year, month) is the end's."""
    if m := re.fullmatch(r"(\d{1,2})(?:er|st|nd|rd|th)?", text):
        month = end.replace(day=1)
        if int(m[1]) > end.day:
            month = _add_months(month, -1)
        try:
            return month.replace(day=int(m[1]))
        except ValueError:
            return None
    found = _point(text, 0, today, ref=end)
    return found[0] if found and found[2] == len(text) else None


_SINCE = re.compile(
    rf"(?<![\w-])(?:since|depuis)\s+(?:the\s+|le\s+|la\s+|l{_APOS})?", re.IGNORECASE
)


def _since(query: str, today: date) -> tuple[Period, str] | None:
    """ "since September", "depuis le 14 mars", "since last week": up to today."""
    for m in _SINCE.finditer(query):
        found = _point(query, m.end(), today)
        if found and found[0] <= today:
            period = Period(found[0], today, query[m.start() : found[2]])
            return period, _without(query, m.start(), found[2])
    return None


_WEEK_OF = re.compile(
    r"(?<![\w-])(?:(?:the\s+)?week\s+of\s+(?:the\s+)?|la\s+semaine\s+du\s+)",
    re.IGNORECASE,
)


def _week_of(query: str, today: date) -> tuple[Period, str] | None:
    """ "la semaine du 14 mars 2024", "the week of March 14"."""
    for m in _WEEK_OF.finditer(query):
        if found := _point(query, m.end(), today):
            start, end = _unit_containing("week", found[0])
            period = Period(start, end, query[m.start() : found[2]])
            return period, _without(query, m.start(), found[2])
    return None


_OFFSET = re.compile(
    rf"(?<![\w-])(?:{_N} {_UNIT}|(?:the |la |le |l{_APOS})?"
    r"(?P<one>day|week|month|year|jour|semaine|mois|année))"
    r" (?P<dir>before|after|avant|après|apres) (?:the |le |l['’])?"
    r"|(?<![\w-])(?:la (?P<eve>veille)|le (?P<next>lendemain)) (?:du |de l['’]|de la )?",
    re.IGNORECASE,
)


def _offset(query: str, today: date) -> tuple[Period, str] | None:
    """ "two weeks before 2024-04-09", "3 jours après le 14 mars", "la veille du 14 mars":
    the unit containing the day reached."""
    for m in _OFFSET.finditer(query):
        found = _point(query, m.end(), today)
        if found is None:
            continue
        groups = m.groupdict()
        if groups["eve"] or groups["next"]:
            unit, n, before = "day", 1, bool(groups["eve"])
        else:
            unit = _UNITS[(groups["unit"] or groups["one"]).lower()]
            n = _number(groups) if groups["n"] else 1
            before = groups["dir"].lower() in ("before", "avant")
        day = _shift(unit, found[0], n) if before else _shift(unit, found[1], -n)
        start, end = _unit_containing(unit, day)
        period = Period(start, end, query[m.start() : found[2]])
        return period, _without(query, m.start(), found[2])
    return None


def _without(query: str, start: int, end: int) -> str:
    return re.sub(r"\s+", " ", query[:start] + " " + query[end:]).strip()


def _days_ago(n: int, today: date) -> tuple[date, date]:
    day = today - timedelta(days=n)
    return day, day


def _unit_containing(unit: str, day: date) -> tuple[date, date]:
    if unit == "day":
        return day, day
    if unit == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if unit == "month":
        start = day.replace(day=1)
        return start, _add_months(start, 1) - timedelta(days=1)
    if unit == "quarter":
        start = date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
        return start, _add_months(start, 3) - timedelta(days=1)
    return date(day.year, 1, 1), date(day.year, 12, 31)


def _shift(unit: str, day: date, n: int) -> date:
    """`day` moved back by `n` units."""
    if unit == "day":
        return day - timedelta(days=n)
    if unit == "week":
        return day - timedelta(weeks=n)
    if unit == "month":
        return _add_months(day, -n)
    if unit == "quarter":
        return _add_months(day, -3 * n)
    return _add_months(day, -12 * n)


def _add_months(day: date, n: int) -> date:
    month = day.month - 1 + n
    year, month = day.year + month // 12, month % 12 + 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise AssertionError


def _number(groups: dict) -> int:
    n = groups["n"].lower()
    return int(n) if n.isdigit() else _NUMBERS[n]


def _ago(groups: dict, today: date) -> tuple[date, date]:
    unit = _UNITS[groups["unit"].lower()]
    return _unit_containing(unit, _shift(unit, today, _number(groups)))


def _past(groups: dict, today: date) -> tuple[date, date]:
    """The last n units, today included."""
    unit = _UNITS[groups["unit"].lower()]
    return _shift(unit, today, _number(groups)) + timedelta(days=1), today


def _last(groups: dict, today: date) -> tuple[date, date]:
    unit = _UNITS[groups["unit"].lower()]
    return _unit_containing(unit, _shift(unit, today, 1))


def _this(groups: dict, today: date) -> tuple[date, date]:
    start, _ = _unit_containing(_UNITS[groups["unit"].lower()], today)
    return start, today


def _weekday(groups: dict, today: date) -> tuple[date, date]:
    """The latest such weekday before today."""
    back = (today.weekday() - _WEEKDAYS[groups["weekday"].lower()] - 1) % 7 + 1
    return _days_ago(back, today)


def _month(groups: dict, today: date) -> tuple[date, date]:
    month = MONTHS[groups["mon"].lower()]
    if groups.get("y"):
        year = int(groups["y"])
    else:  # the latest such month, up to this one
        year = today.year if month <= today.month else today.year - 1
    return _unit_containing("month", date(year, month, 1))


def _year(groups: dict, today: date) -> tuple[date, date]:
    return _unit_containing("year", date(int(groups["y"]), 1, 1))


def _quarter(groups: dict, today: date) -> tuple[date, date]:
    q = int(groups["q"]) if groups.get("q") else _QUARTERS[groups["qw"].lower()]
    if groups.get("y"):
        year = int(groups["y"])
    else:  # the latest such quarter, up to this one
        year = today.year if q <= (today.month - 1) // 3 + 1 else today.year - 1
    return _unit_containing("quarter", date(year, 3 * q - 2, 1))


def _season_of(groups: dict, year: int) -> tuple[date, date]:
    start = date(year, _SEASONS[groups["season"].lower()], 1)
    return start, _add_months(start, 3) - timedelta(days=1)


def _season(groups: dict, today: date) -> tuple[date, date]:
    """The season of the year given, else the latest that has started."""
    if groups.get("y"):
        return _season_of(groups, int(groups["y"]))
    start, end = _season_of(groups, today.year)
    return (start, end) if start <= today else _season_of(groups, today.year - 1)


def _last_season(groups: dict, today: date) -> tuple[date, date]:
    """The latest such season that has ended."""
    start, end = _season_of(groups, today.year)
    return (start, end) if end < today else _season_of(groups, today.year - 1)


def _last_weekend(groups: dict, today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=2), monday - timedelta(days=1)


def _this_weekend(groups: dict, today: date) -> tuple[date, date]:
    """The weekend under way, else the latest: notes are about the past."""
    if today.weekday() >= 5:
        return today - timedelta(days=today.weekday() - 5), today
    return _last_weekend(groups, today)


def _rule(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){pattern}(?![\w-])", re.IGNORECASE)


_RELATIVE = [
    (
        _rule(r"(?:the )?day before yesterday|avant[- ]hier"),
        lambda g, t: _days_ago(2, t),
    ),
    (_rule(r"yesterday|hier"), lambda g, t: _days_ago(1, t)),
    (
        _rule(rf"today|this morning|aujourd{_APOS}hui|ce matin"),
        lambda g, t: _days_ago(0, t),
    ),
    (
        _rule(r"(?:the |over the )?(?:last|past) fortnight"),
        lambda g, t: (t - timedelta(days=13), t),
    ),
    (
        _rule(
            rf"(?:the )?(?:last|past|previous) week-?end|(?:le )?week-?end (?:{_LAST_FR}|passé)"
        ),
        _last_weekend,
    ),
    (_rule(r"this week-?end|ce week-?end"), _this_weekend),
    (
        _rule(
            r"since the (?:start|beginning) of (?:the |this )?(?P<unit>week|month|quarter|year)"
        ),
        _this,
    ),
    (
        _rule(
            rf"depuis (?:le )?d[ée]but (?:de la |du |de l{_APOS})(?P<unit>semaine|mois|trimestre|année|annee)"
        ),
        _this,
    ),
    (_rule(rf"depuis {_N} {_UNIT}"), _past),
    (_rule(rf"{_N} {_UNIT} ago"), _ago),
    (_rule(rf"il y a {_N} {_UNIT}"), _ago),
    (_rule(rf"(?:the )?(?:last|past) {_N} {_UNIT}"), _past),
    (_rule(rf"(?:les|ces) {_N} {_LAST_FR}s {_UNIT}"), _past),
    (_rule(rf"(?:last|previous|on) {_DAY}"), _weekday),
    (_rule(rf"{_DAY} {_LAST_FR}"), _weekday),
    (
        _rule(r"(?:the )?(?:last|previous|past) (?P<unit>week|month|quarter|year)"),
        _last,
    ),
    (
        _rule(
            rf"(?:la |le |l{_APOS})(?P<unit>semaine|mois|trimestre|année|an) {_LAST_FR}"
        ),
        _last,
    ),
    (_rule(r"this (?P<unit>week|month|quarter|year)"), _this),
    (_rule(r"(?:cette |ce )(?P<unit>semaine|mois|trimestre|année)(?:-ci)?"), _this),
]
# Quarters and seasons, by name.
_NAMED = [
    (
        _rule(r"(?:(?:in|en|during|pendant|au) )?Q(?P<q>[1-4])(?:[ -](?P<y>\d{4}))?"),
        _quarter,
    ),
    (_rule(r"T(?P<q>[1-4])[ -](?P<y>\d{4})"), _quarter),  # a bare "T1" is too often not
    (_rule(rf"(?:the )?{_QUARTER} quarter(?: of)?(?: (?P<y>\d{{4}}))?"), _quarter),
    (_rule(rf"(?:le |au )?{_QUARTER} trimestre(?: (?:de )?(?P<y>\d{{4}}))?"), _quarter),
    (_rule(rf"(?:last|previous) {_SEASON}"), _last_season),
    (_rule(rf"(?:l{_APOS}|le )?{_SEASON} {_LAST_FR}"), _last_season),
    (_rule(rf"(?:this|cet|ce) {_SEASON}"), _season),
    (
        _rule(
            rf"(?:(?:in|during|en|pendant|au|à) )?(?:the |l{_APOS}|le )?{_SEASON}"
            rf"(?: of| de)? (?P<y>\d{{4}})"
        ),
        _season,
    ),
    # A bare season name is too often a word ("fall"): only after a preposition.
    (_rule(rf"(?:in|during|en|pendant|au|à) (?:the |l{_APOS}|le )?{_SEASON}"), _season),
]
_CALENDAR = [
    (_rule(rf"(?:(?:in|en|during|pendant) )?{_MON} (?P<y>\d{{4}})"), _month),
    # A bare month name is too often a word ("may", "mars"): only after a preposition.
    (_rule(rf"(?:in|en|during|pendant) {_MON}"), _month),
    (_rule(r"(?:in|en|during|pendant) (?P<y>(?:19|20)\d{2})"), _year),
]
# Only where a period is expected: "since September", "between 2023 and 2024".
_BARE = [
    (_rule(rf"{_MON}(?: (?P<y>\d{{4}}))?"), _month),
    (_rule(r"(?P<y>(?:19|20)\d{2})"), _year),
]
