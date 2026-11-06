"""What `ask --all` and `--first` read and when they stop; the model is faked: which days
it says yes of is what scripts/eval.py measures."""

from pathlib import Path

import numpy as np
import pytest

from ogmios import ask, scan, store
from ogmios.ask import Excerpt
from ogmios.config import Source
from ogmios.search import earliest_matches, search

FIXTURE = Path(__file__).parent / "fixtures" / "done.md"


class FakeEmbedder:
    name = "fake"

    def passages(self, texts):
        return np.ones((len(list(texts)), 4), dtype=np.float32)


class FakeLLM:
    """Says what `says` says of the note it is given."""

    def __init__(self, says):
        self.says = says
        self.calls = 0

    def create_chat_completion(self, chat, **_):
        self.calls += 1
        return {
            "choices": [{"message": {"content": self.says(chat[1]["content"])}}],
            "usage": {"completion_tokens": 1},
        }


@pytest.fixture
def db(tmp_path):
    connection = store.connect(tmp_path / "index.sqlite")
    files = store.collect_files([Source(path=FIXTURE)])
    store.rebuild(connection, files, FakeEmbedder())  # ty: ignore[invalid-argument-type]
    return connection


def days(n, text="- something else"):
    return [
        Excerpt("f.md", i, f"2024-{1 + i // 28:02}-{1 + i % 28:02}", "", text)
        for i in range(n)
    ]


def scanned(mode, excerpts, says, topic="kafka"):
    state = scan.Scan(mode, topic, "", excerpts)
    llm = FakeLLM(says)
    list(scan.run(llm, state))  # ty: ignore[invalid-argument-type]
    return state, llm


def says_yes_of(word):
    return lambda note: "yes" if word in note.split("Asked about")[0] else "no"


@pytest.mark.parametrize(
    ("question", "subject"),
    [
        ("when was the first time I talked about terraform?", "terraform"),
        ("when was the first time I applied terraform?", "I applied terraform"),
        ("when did I first deploy kafka", "when did I deploy kafka"),
        ("list every time I worked on terraform", "I worked on terraform"),
        ("all occurrences of NET-881", "NET-881"),
        ("quelle a été la première fois que j'ai parlé de kafka ?", "kafka"),
        ("la première fois que j'ai utilisé vault", "j'ai utilisé vault"),
        ("toutes les fois où j'ai travaillé sur kafka", "j'ai travaillé sur kafka"),
        ("kafka", "kafka"),
        ("first time", "first time"),  # nothing else: as it was
    ],
)
def test_subject(question, subject):
    assert scan.subject(question) == subject


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("yes", "yes"),
        ("No.", "no"),
        ("**mention**", "mention"),
        ("Yes, it does", "yes"),
        ("nothing", None),
        ("", None),
    ],
)
def test_label(text, label):
    assert scan.label_of(text) == label


def test_earliest_matches_are_the_oldest_days_of_each_word(db):
    hits = earliest_matches(db, "NET-881", 2)
    assert [h.date for h in hits] == ["2024-03-14", "2024-04-02"]
    assert "NET-881" in hits[0].snippet


def test_earliest_matches_hold_the_chunk_to_read(db):
    excerpts = ask.best_parts(db, earliest_matches(db, "RESEAU-4512", 1), 1000)
    assert [e.date for e in excerpts] == ["2022-04-04"]
    assert "RESEAU-4512" in excerpts[0].text


def test_first_reads_the_oldest_day_first(db):
    hits, _ = scan.candidates(db, None, "NET-881", scan.FIRST, None, None)
    dates = [h.date or "" for h in hits]
    assert dates == sorted(dates)
    assert dates[0] == "2024-03-14"


def test_all_reads_the_best_day_first(db):
    hits, _ = scan.candidates(db, None, "RESEAU-4512 validé", scan.ALL, None, None)
    best = search(db, None, "RESEAU-4512 validé")
    assert [h.section for h in hits] == [h.section for h in best]


def test_all_reads_every_day_found_however_low_it_ranks():
    excerpts = days(40)
    excerpts[35].text = "- kafka"
    state, llm = scanned(scan.ALL, excerpts, says_yes_of("kafka"))
    assert llm.calls == 40
    assert state.stopped == ""
    assert [v.excerpt for v in state.yes] == [excerpts[35]]
    assert scan.coverage(state) == "Checked all 40 days found."


def test_all_has_a_limit_and_says_so():
    state, llm = scanned(scan.ALL, days(scan.MAX_DAYS + 5), lambda note: "yes")
    assert llm.calls == scan.MAX_DAYS
    assert state.stopped == "limit"
    assert "there may be more" in scan.coverage(state)


def test_all_puts_what_it_found_in_date_order():
    late, early = days(2, "- kafka")[::-1]
    state, _ = scanned(scan.ALL, [late, early], says_yes_of("kafka"))
    closing = scan.conclusion(state)
    assert closing[1] == "In date order:"
    assert closing[2].startswith(early.date)
    assert closing[3].startswith(late.date)


def test_all_in_date_order_already_says_it_once():
    state, _ = scanned(scan.ALL, days(2, "- kafka"), says_yes_of("kafka"))
    assert "In date order:" not in scan.conclusion(state)


def test_first_reads_on_to_the_end_of_the_group_and_stops():
    excerpts = days(40)
    excerpts[3].text = "- kafka"
    state, llm = scanned(scan.FIRST, excerpts, says_yes_of("kafka"))
    assert llm.calls == scan.GROUP_DAYS
    assert state.stopped == "found"
    assert scan.first_day(state).excerpt is excerpts[3]  # ty: ignore
    assert f"First in the days checked: {excerpts[3].date} [4]" in scan.conclusion(
        state
    )


def test_first_is_not_stopped_by_a_yes_without_the_words_of_the_question():
    excerpts = days(40)
    excerpts[1].text = "- lunch and learn on data contracts"
    excerpts[25].text = "- kafka"

    def says(note):
        return "yes" if "data contracts" in note or "- kafka" in note else "no"

    state, llm = scanned(scan.FIRST, excerpts, says)
    assert llm.calls == 30
    assert scan.first_day(state).excerpt is excerpts[25]  # ty: ignore
    closing = scan.conclusion(state)
    assert f"Possibly earlier, in other words: {excerpts[1].date} [2]" in closing
    assert f"First in the days checked: {excerpts[25].date} [26]" in closing


def test_first_in_other_words_only_is_still_an_answer():
    excerpts = days(5)
    excerpts[2].text = "- event streaming"
    state, _ = scanned(scan.FIRST, excerpts, says_yes_of("event streaming"))
    assert scan.first_day(state).excerpt is excerpts[2]  # ty: ignore


def test_first_shows_what_came_before_it():
    excerpts = days(5)
    excerpts[1].text = "- should try kafka someday"
    excerpts[3].text = "- deployed kafka"

    def says(note):
        return "mention" if "try" in note else "yes" if "deployed" in note else "no"

    state, _ = scanned(scan.FIRST, excerpts, says)
    closing = scan.conclusion(state)
    assert f"Earliest mention: {excerpts[1].date} [2]" in closing
    assert f"First in the days checked: {excerpts[3].date} [4]" in closing


def test_nothing_found_is_said():
    state, _ = scanned(scan.FIRST, days(3), lambda note: "no")
    assert "No day checked shows it." in scan.conclusion(state)
    assert scan.first_day(state) is None


def test_days_left_out_are_said():
    excerpts = days(15)
    excerpts[0].text = "- kafka"
    excerpts[-1].date = None
    state, _ = scanned(scan.FIRST, excerpts, says_yes_of("kafka"))
    assert "1 without a date not checked" in scan.coverage(state)


def test_a_day_without_a_verdict_is_said():
    state, _ = scanned(scan.ALL, days(2), lambda note: "hmm")
    assert "no verdict on 2" in scan.coverage(state)


def test_quote_is_the_line_of_the_note_that_matches():
    excerpt = Excerpt("f.md", 1, "2024-01-01", "", "- lunch\n…\n- deployed Kafka\n…")
    assert scan.quote(excerpt, "kafka") == "deployed Kafka"
    long = Excerpt("f.md", 1, "2024-01-01", "", "- kafka " + "x" * 300)
    assert len(scan.quote(long, "kafka")) == scan.QUOTE_CHARS + 1


def test_cited_numbers_are_the_excerpts_read():
    excerpts = days(3, "- kafka")
    state, _ = scanned(scan.FIRST, excerpts, says_yes_of("kafka"))
    text = "\n".join(scan.line(state, v) for v in state.yes)
    assert ask.cited(text, state.read) == excerpts


def test_days_found_but_not_read_are_counted(db):
    hits, found = scan.candidates(db, None, "RESEAU-4512", scan.ALL, None, 2)
    assert (len(hits), found) == (2, 3)
    state = scan.Scan(scan.ALL, "RESEAU-4512", "", days(2), found)
    state.checked = 2
    assert (
        "Checked 2 of 3 days found, best first: there may be more in the others"
        in scan.coverage(state)
    )


def test_a_search_at_its_limit_says_there_may_be_more_days():
    state = scan.Scan(scan.ALL, "x", "", days(2), scan.SEARCH_DAYS)
    state.checked = 2
    assert f"2 of {scan.SEARCH_DAYS}+ days" in scan.coverage(state)
