from datetime import date

import pytest

from ogmios.dates import delimiter_candidate, detect_delimiters, find_date


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("## 2024-05-14", date(2024, 5, 14)),
        ("12/03/2023", date(2023, 3, 12)),
        ("--- 12.03.23 ---", date(2023, 3, 12)),
        ("Mardi 3 janvier 2023", date(2023, 1, 3)),
        ("Mercredi 1er février 2023", date(2023, 2, 1)),
        ("**March 6, 2024**", date(2024, 3, 6)),
        ("# Thursday, 7th March 2024", date(2024, 3, 7)),
        ("## 2025-01-08 - Reprise", date(2025, 1, 8)),
    ],
)
def test_delimiter_formats(line, expected):
    candidate = delimiter_candidate(0, line)
    assert candidate is not None
    assert candidate.date == expected


@pytest.mark.parametrize(
    "line",
    [
        "- deadline is 12/03/2023 for the migration of the APIs",
        "Upgrade from 2.6 to 2.8 planned",
        "1.5",
        "## Airflow",
    ],
)
def test_not_delimiters(line):
    assert delimiter_candidate(0, line) is None


def test_label_keeps_non_date_text():
    candidate = delimiter_candidate(0, "## 2025-01-08 - Reprise")
    assert candidate is not None
    assert candidate.label == "Reprise"
    weekday = delimiter_candidate(0, "Mardi 3 janvier 2023")
    assert weekday is not None
    assert weekday.label == ""


def test_month_first_when_configured():
    match = find_date("03/12/2023", day_first=False)
    assert match is not None
    assert (match.month, match.day) == (3, 12)


def test_missing_year_inferred_from_neighbours():
    lines = [
        "Lundi 30 décembre 2024",
        "x",
        "Jeudi 2 janvier",
        "x",
        "Vendredi 3 janvier 2025",
    ]
    found = detect_delimiters(lines)
    assert [d.date for d in found] == [
        date(2024, 12, 30),
        date(2025, 1, 2),
        date(2025, 1, 3),
    ]


def test_missing_year_in_descending_file():
    lines = [
        "3 janvier 2025",
        "x",
        "2 janvier",
        "x",
        "30 décembre",
        "x",
        "28 décembre 2024",
    ]
    found = detect_delimiters(lines)
    assert [d.date for d in found] == [
        date(2025, 1, 3),
        date(2025, 1, 2),
        date(2024, 12, 30),
        date(2024, 12, 28),
    ]


def test_out_of_order_plain_line_is_dropped():
    lines = ["2024-01-01", "x", "2019-06-01", "x", "2024-01-03", "x", "2024-01-04"]
    found = detect_delimiters(lines)
    assert [d.line for d in found] == [0, 4, 6]


def test_out_of_order_heading_is_kept():
    lines = ["## 2024-01-01", "x", "## 2019-06-01", "x", "## 2024-01-03"]
    assert len(detect_delimiters(lines)) == 3
