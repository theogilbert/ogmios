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
    ],
)
def test_periods(query, period, rest):
    found = find_period(query, TODAY)
    assert found is not None
    assert (str(found[0]), found[1]) == (period, rest)


@pytest.mark.parametrize(
    "query", ["NET-881", "may I open the flows", "mars rover", "2023"]
)
def test_no_period(query):
    assert find_period(query, TODAY) is None
