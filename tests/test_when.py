from datetime import date

import pytest

from ogmios.when import find_period

TODAY = date(2026, 10, 27)  # a Tuesday


@pytest.mark.parametrize(
    ("query", "period", "rest"),
    [
        ("what did I do yesterday", "2026-10-26", "what did I do"),
        ("qu'ai-je fait hier ?", "2026-10-26", "qu'ai-je fait ?"),
        ("avant-hier", "2026-10-25", ""),
        ("aujourd'hui", "2026-10-27", ""),
        ("last week sftp", "2026-10-19 to 2026-10-25", "sftp"),
        ("la semaine dernière", "2026-10-19 to 2026-10-25", ""),
        ("ce mois-ci", "2026-10-01 to 2026-10-27", ""),
        ("il y a deux semaines", "2026-10-12 to 2026-10-18", ""),
        ("les 3 derniers jours", "2026-10-25 to 2026-10-27", ""),
        ("last friday", "2026-10-23", ""),
        ("in March 2024", "2024-03-01 to 2024-03-31", ""),
        ("en novembre", "2025-11-01 to 2025-11-30", ""),  # the latest November
        ("14 mars 2024 flux", "2024-03-14", "flux"),
        ("in 2023 airflow", "2023-01-01 to 2023-12-31", "airflow"),
        # Ranges, and what is built on another expression
        ("between March and May 2024", "2024-03-01 to 2024-05-31", ""),
        ("entre le 3 et le 10 avril 2024", "2024-04-03 to 2024-04-10", ""),
        ("du 3 au 10 avril 2024 flux", "2024-04-03 to 2024-04-10", "flux"),
        ("from 2024-03-01 to 2024-03-15", "2024-03-01 to 2024-03-15", ""),
        ("de novembre à février", "2025-11-01 to 2026-02-28", ""),
        ("la semaine du 14 mars 2024", "2024-03-11 to 2024-03-17", ""),
        ("the week of March 14, 2024", "2024-03-11 to 2024-03-17", ""),
        ("two weeks before 2024-04-09", "2024-03-25 to 2024-03-31", ""),
        ("3 jours après le 14 mars 2024", "2024-03-17", ""),
        ("la veille du 14 mars 2024", "2024-03-13", ""),
        ("since September", "2026-09-01 to 2026-10-27", ""),
        ("depuis le 14 mars", "2026-03-14 to 2026-10-27", ""),
        ("since last week sftp", "2026-10-19 to 2026-10-27", "sftp"),
        ("depuis le début de l'année", "2026-01-01 to 2026-10-27", ""),
        ("since the beginning of the month", "2026-10-01 to 2026-10-27", ""),
        ("depuis 3 semaines", "2026-10-07 to 2026-10-27", ""),
        # Quarters, seasons, weekends
        ("Q1 2024", "2024-01-01 to 2024-03-31", ""),
        ("au premier trimestre 2024", "2024-01-01 to 2024-03-31", ""),
        ("in Q3", "2026-07-01 to 2026-09-30", ""),
        ("last quarter", "2026-07-01 to 2026-09-30", ""),
        ("le trimestre dernier", "2026-07-01 to 2026-09-30", ""),
        ("ce trimestre", "2026-10-01 to 2026-10-27", ""),
        ("il y a deux trimestres", "2026-04-01 to 2026-06-30", ""),
        ("last summer", "2026-06-01 to 2026-08-31", ""),
        (
            "qu'ai-je fait l'été dernier ?",
            "2026-06-01 to 2026-08-31",
            "qu'ai-je fait ?",
        ),
        ("in the spring of 2023", "2023-03-01 to 2023-05-31", ""),
        ("l'hiver dernier", "2025-12-01 to 2026-02-28", ""),
        ("en hiver", "2025-12-01 to 2026-02-28", ""),
        ("last weekend", "2026-10-24 to 2026-10-25", ""),
        ("le week-end dernier", "2026-10-24 to 2026-10-25", ""),
        ("ce week-end", "2026-10-24 to 2026-10-25", ""),
        ("the past fortnight", "2026-10-14 to 2026-10-27", ""),
    ],
)
def test_periods(query, period, rest):
    found = find_period(query, TODAY)
    assert found is not None
    assert (str(found[0]), found[1]) == (period, rest)


@pytest.mark.parametrize(
    "query",
    [
        "NET-881",
        "may I open the flows",
        "mars rover",
        "2023",
        "notes from the network team",
        "flux entre srv-sftp-part01 et le cluster",
        "the fall of the API",
        "since the outage",
        "Q&A with the platform team",
    ],
)
def test_no_period(query):
    assert find_period(query, TODAY) is None
