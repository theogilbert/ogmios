"""Time expressions in queries ("yesterday", "la semaine dernière", "in March 2024").

Embeddings know nothing of today's date, so "what did I do yesterday" would otherwise
match any day that reads like an answer. The expression is turned into a date range that
filters the search, and taken out of the query.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .dates import MONTHS, find_date

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
    "year": "year", "years": "year", "an": "year", "ans": "year", "année": "year",
    "années": "year",
}  # fmt: skip

_N = rf"(?P<n>\d+|{'|'.join(_NUMBERS)})"
_UNIT = rf"(?P<unit>{'|'.join(sorted(_UNITS, key=len, reverse=True))})"
_DAY = rf"(?P<weekday>{'|'.join(_WEEKDAYS)})"
_MON = rf"(?P<mon>{'|'.join(sorted(MONTHS, key=len, reverse=True))})"
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
    found = _match(_RELATIVE, query, today) or _day(query, today)
    return found or _match(_CALENDAR, query, today)


def _match(rules: list, query: str, today: date) -> tuple[Period, str] | None:
    for pattern, resolve in rules:
        if m := pattern.search(query):
            start, end = resolve(m.groupdict(), today)
            return Period(start, end, m.group(0)), _without(query, m.start(), m.end())
    return None


def _day(query: str, today: date) -> tuple[Period, str] | None:
    """An explicit date: "2024-03-14", "14 mars", "March 14, 2024"."""
    m = find_date(query)
    if m is None:
        return None
    year = m.year
    if year is None:  # the latest such day, up to today
        year = today.year - ((m.month, m.day) > (today.month, today.day))
    try:
        day = date(year, m.month, m.day)
    except ValueError:  # February 29th
        return None
    return Period(day, day, query[m.start : m.end]), _without(query, m.start, m.end)


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
    return date(day.year, 1, 1), date(day.year, 12, 31)


def _shift(unit: str, day: date, n: int) -> date:
    """`day` moved back by `n` units."""
    if unit == "day":
        return day - timedelta(days=n)
    if unit == "week":
        return day - timedelta(weeks=n)
    if unit == "month":
        return _add_months(day, -n)
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
    (_rule(rf"{_N} {_UNIT} ago"), _ago),
    (_rule(rf"il y a {_N} {_UNIT}"), _ago),
    (_rule(rf"(?:the )?(?:last|past) {_N} {_UNIT}"), _past),
    (_rule(rf"(?:les|ces) {_N} {_LAST_FR}s {_UNIT}"), _past),
    (_rule(rf"(?:last|previous|on) {_DAY}"), _weekday),
    (_rule(rf"{_DAY} {_LAST_FR}"), _weekday),
    (_rule(r"(?:the )?(?:last|previous|past) (?P<unit>week|month|year)"), _last),
    (_rule(rf"(?:la |le |l{_APOS})(?P<unit>semaine|mois|année|an) {_LAST_FR}"), _last),
    (_rule(r"this (?P<unit>week|month|year)"), _this),
    (_rule(r"(?:cette |ce )(?P<unit>semaine|mois|année)(?:-ci)?"), _this),
]
_CALENDAR = [
    (_rule(rf"(?:(?:in|en|during|pendant) )?{_MON} (?P<y>\d{{4}})"), _month),
    # A bare month name is too often a word ("may", "mars"): only after a preposition.
    (_rule(rf"(?:in|en|during|pendant) {_MON}"), _month),
    (_rule(r"(?:in|en|during|pendant) (?P<y>(?:19|20)\d{2})"), _year),
]
