from pathlib import Path

from ogmios.parse import SplitOptions, chunk_section, parse_file, split_blocks

FIXTURE = Path(__file__).parent / "fixtures" / "done.md"


def _parse(text: str, name: str = "notes.md", **options):
    lines = text.splitlines()
    return lines, parse_file(Path(name), lines, SplitOptions(**options))


def test_fixture_mixes_three_date_formats():
    lines = FIXTURE.read_text().splitlines()
    sections = parse_file(FIXTURE, lines, SplitOptions())
    dates = [s.date for s in sections if s.date]
    assert dates[0] == "2022-03-14"
    assert "2023-01-03" in dates
    assert "2024-03-06" in dates
    assert dates[-1] == "2025-02-03"
    assert dates == sorted(dates)
    # The preamble before the first date is kept as its own section.
    assert sections[0].date is None


def test_quote_blocks_group_items_under_their_project():
    text = """\
## 2024-03-06
> ingestion-api
  > migrated the healthchecks
  > readiness probe too aggressive
> airflow
  > upgrade planned
## 2024-03-07
## 2024-03-08"""
    _lines, sections = _parse(text)
    blocks = sections[0].blocks
    assert [(b.title, b.start, b.end) for b in blocks] == [
        ("ingestion-api", 1, 4),
        ("airflow", 4, 6),
    ]


def test_list_items_with_paragraphs():
    lines = [
        "- budget",
        "  Prepared the cost review.",
        "  BigQuery is the main driver.",
        "- bigquery",
        "  Proposed slot reservations.",
    ]
    blocks = split_blocks(lines, 0, len(lines))
    assert [(b.title, b.start, b.end) for b in blocks] == [
        ("budget", 0, 3),
        ("bigquery", 3, 5),
    ]


def test_paragraphs_separated_by_blank_lines():
    lines = ["First paragraph", "continues here", "", "Second paragraph"]
    blocks = split_blocks(lines, 0, len(lines))
    assert [(b.start, b.end) for b in blocks] == [(0, 2), (3, 4)]


def test_horizontal_rules_are_not_blocks():
    lines = ["- item", "", "---", ""]
    assert len(split_blocks(lines, 0, len(lines))) == 1


def test_undated_file_splits_on_headings():
    text = "# Title\nintro\n## Kubernetes\nnotes\n## Terraform\nmore notes"
    _, sections = _parse(text, "infra.md")
    assert [s.title for s in sections] == [
        "infra › Title",
        "infra › Kubernetes",
        "infra › Terraform",
    ]


def test_configured_delimiters_override_detection():
    import re

    text = "DAY one\nfoo\nDAY two\nbar"
    _, sections = _parse(text, delimiters=[re.compile(r"^DAY ")])
    assert [s.title for s in sections] == ["DAY one", "DAY two"]


def test_chunks_cover_section_blocks_and_pairs():
    text = "\n".join(
        ["## 2024-01-01", "- a", "  x", "- b", "  y", "- c", "  z"]
        + ["## 2024-01-02"] * 3
    )
    lines, sections = _parse(text)
    kinds = [c.kind for c in chunk_section(lines, sections[0])]
    assert kinds == ["section", "block", "block", "block", "pair", "pair"]
    assert all(c.context == "2024-01-01" for c in chunk_section(lines, sections[0]))


def test_long_blocks_are_windowed_with_their_title():
    body = [f"  line {i} " + "x" * 80 for i in range(40)]
    text = "\n".join(
        [
            "## 2024-01-01",
            "- project",
            *body,
            "- other",
            "## 2024-01-02",
            "## 2024-01-03",
        ]
    )
    lines, sections = _parse(text)
    windows = [c for c in chunk_section(lines, sections[0]) if c.kind == "window"]
    assert len(windows) > 1
    assert windows[1].context == "2024-01-01 · project"
