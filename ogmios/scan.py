"""Questions one read cannot answer: every time something happened, or the first time.

The model judges the days found one at a time, in a context as short as can be: given ten
at once, it skips some, mixes them up, and takes one's subject for another's. Its prompt
stays in llama.cpp's cache, so that a day costs little more than reading its few lines.
What it finds is then put together by the code, which cannot drop a day or misquote a date.

Search scores only say how a day ranks, and a day that says it in other words ranks low:
past the first ones, rank says little of what a day holds. So to list every time, every
day found is read, up to a limit. What was not read is said in the answer.
"""

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import ask
from .ask import Excerpt
from .search import Hit, best_line, earliest_matches, merge, query_terms, search
from .when import Period

if TYPE_CHECKING:
    import llama_cpp

    from .embed import Embedder

ALL = "all"
FIRST = "first"

# A first time is looked for this many days at a time: what follows it is worth seeing.
GROUP_DAYS = 10
MAX_DAYS = 100  # at ~0.5 to 3 s each
DAY_CHARS = 1_000  # what the matching parts of a day may take
SEARCH_DAYS = 200
SEARCH_CHUNKS = 5_000  # chunks per ranking: a day in other words is far down
# Asked for the first time, the oldest days holding each word of the question, however
# low they rank, go with the most relevant days.
EARLIEST_PER_TERM = 3

YES, MENTION, NO = "yes", "mention", "no"

SYSTEM = """\
You check one excerpt of the user's own work notes, written in English and French, against what they ask about. "…" marks lines left out.
Notes are terse: a few words may be all a note says of work done that day.
Answer with one word:
yes: the note shows what is asked about. Asked about a subject alone, any note about it is yes.
mention: it names the subject, but only as an idea, a plan or in passing, not what is asked about.
no: nothing in it is about the subject, even when it shares a word."""
JUDGE_TOKENS = 4  # a word, and what a model may put around it

_LABEL_RE = re.compile(r"\b(yes|mention|no)\b", re.IGNORECASE)
QUOTE_CHARS = 160
# What asks for the first time or for every time, not what about. Longest first.
_FRAMING_RE = re.compile(
    r"\b(?:"
    r"when (?:was|is) the (?:very )?first time"
    r"|what (?:was|is) the (?:very )?first time"
    r"|(?:the )?(?:very )?first time"
    r"|first|earliest"
    r"|list (?:all|every|each)(?: the)?(?: times?| occurrences?(?: of)?)?"
    r"|(?:all )?(?:the )?occurrences?(?: of)?"
    r"|(?:every|each) time|all the times"
    r"|(?:quelle|quand) (?:a été|est|était) la (?:toute )?premi[eè]re fois"
    r"|c'[eé]tait quand la premi[eè]re fois"
    r"|(?:la )?(?:toute )?premi[eè]re fois"
    r"|(?:liste )?toutes les (?:fois|occurrences)(?: de)?"
    r"|(?:[aà] )?chaque fois"
    r"|liste"
    r"|(?:i )?(?:talked|spoke|wrote) (?:about|of)|(?:i )?mentioned"
    r"|(?:que )?(?:j'ai )?(?:parl[eé]|[eé]crit|discut[eé]) (?:de|du|d'|sur)"
    r"|(?:que )?(?:j'ai )?mentionn[eé]"
    r")(?!\w)",
    re.IGNORECASE,
)


def subject(question: str) -> str:
    """The question without the words that ask for the first or every time: they would
    be searched as if they were what it is about. The question itself if nothing else
    is left."""
    text = " ".join(_FRAMING_RE.sub(" ", question).split()).strip(" ?.,:;!")
    # What the French framing leaves: "... fois où j'ai", "... fois que j'ai"
    text = re.sub(r"^(?:o[uù]|que|qu')\s*", "", text, flags=re.IGNORECASE)
    return text or question


def candidates(
    db: sqlite3.Connection,
    embedder: Embedder | None,
    topic: str,
    mode: str,
    period: Period | None,
    days: int | None,
    wordings: str = "",
    paths: list[str] | None = None,
) -> tuple[list[Hit], int]:
    """The days to check, in the order to read them: best first to list every time,
    oldest first to find the first, days without a date last. And how many days were
    found, of which these are the ones to read."""

    def find(query: str) -> list[Hit]:
        return search(
            db,
            embedder,
            query,
            SEARCH_DAYS,
            period=period,
            paths=paths,
            closest_line=False,
            candidates=SEARCH_CHUNKS,
        )

    hits = find(topic)
    if wordings:
        hits = merge(hits, find(f"{topic} {wordings}"))
    if mode == ALL:
        return hits[: days or MAX_DAYS], len(hits)
    earliest = earliest_matches(db, topic, EARLIEST_PER_TERM, period, paths)
    seen = {h.section for h in earliest}
    best = [h for h in hits if h.section not in seen]
    kept = [*earliest, *best[: max((days or MAX_DAYS) - len(earliest), 0)]]
    order = sorted(kept, key=lambda h: (h.date is None, h.date or "", h.path, h.line))
    return order, len(earliest) + len(best)


def excerpts_for(db: sqlite3.Connection, hits: list[Hit]) -> list[Excerpt]:
    return ask.best_parts(db, hits, DAY_CHARS)


def messages(topic: str, excerpt: Excerpt) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"{excerpt.date or excerpt.title}\n{excerpt.text}\n\n"
            f"Asked about: {topic}",
        },
    ]


def label_of(text: str) -> str | None:
    """The model's word: the first of them, in case it says more."""
    m = _LABEL_RE.search(text)
    return m[1].lower() if m else None


@dataclass
class Verdict:
    number: int  # the excerpt's, from 1
    excerpt: Excerpt
    label: str


@dataclass
class Scan:
    """What a scan has read and found so far."""

    mode: str
    topic: str  # what the days are checked against
    wordings: str  # also searched, and looked for in what is quoted
    excerpts: list[Excerpt]  # those to read, of the days found
    found: int = 0  # the days the search found, 0 if just those
    verdicts: list[Verdict] = field(default_factory=list)
    checked: int = 0  # the first `checked` excerpts were read
    stopped: str = ""  # "found" | "limit" | "" (read them all)
    chats: list[list[dict]] = field(default_factory=list)
    tokens: int = 0  # written by the model

    @property
    def read(self) -> list[Excerpt]:
        return self.excerpts[: self.checked]

    @property
    def yes(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.label == YES]

    @property
    def mentions(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.label == MENTION]

    @property
    def query(self) -> str:
        return f"{self.topic} {self.wordings}"

    def says_it(self, verdict: Verdict) -> bool:
        """Whether a day judged yes holds a word of the question itself. Not of the other
        wordings, which are broad on purpose: a day in other words is still found and
        reported, but does not end the search."""
        if verdict.label != YES:
            return False
        lines = verdict.excerpt.text.splitlines()
        return best_line(lines, list(range(len(lines))), query_terms(self.topic))[1]


def run(
    llm: llama_cpp.Llama,
    state: Scan,
    on_day=lambda number, total: None,
) -> Iterator[Verdict]:
    """Judges day after day, yielding what it finds, up to MAX_DAYS. To list every time,
    it reads them all. For the first time, it stops at the end of a group that shows it
    in the question's words, whose later days show what came next (the model now and then
    says yes of a day on something else, which must not end the search)."""
    found = False
    for number, excerpt in enumerate(state.excerpts, 1):
        if number > MAX_DAYS:
            state.stopped = "limit"
            return
        on_day(number, len(state.excerpts))
        chat = messages(state.topic, excerpt)
        state.chats.append(chat)
        out = llm.create_chat_completion(
            chat,  # ty: ignore[invalid-argument-type]
            temperature=0,
            max_tokens=JUDGE_TOKENS,
        )
        state.tokens += out["usage"]["completion_tokens"]  # ty: ignore
        label = label_of(out["choices"][0]["message"]["content"] or "")  # ty: ignore
        state.checked = number
        if label is not None:
            verdict = Verdict(number, excerpt, label)
            state.verdicts.append(verdict)
            found |= state.mode == FIRST and state.says_it(verdict)
            yield verdict
        if found and number % GROUP_DAYS == 0:
            state.stopped = "found"
            return
    if found:
        state.stopped = "found"


def quote(excerpt: Excerpt, query: str) -> str:
    """The line of the excerpt that shows why it was picked: the note's own words, which
    a model cannot misquote."""
    lines = [s for s in excerpt.text.splitlines() if s != "…"]
    i, _ = best_line(lines, list(range(len(lines))), query_terms(query))
    text = lines[i].strip().lstrip("-*>• ")
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rstrip() + "…"


def line(state: Scan, verdict: Verdict) -> str:
    e = verdict.excerpt
    label = "(mention) " if verdict.label == MENTION else ""
    said = quote(e, state.query)
    return f"{e.date or e.title}  {label}{said} [{verdict.number}]"


def shown(state: Scan, verdict: Verdict) -> bool:
    """Whether a verdict is a line of the answer: mentions matter to a first time,
    as what came before it."""
    return verdict.label == YES or (state.mode == FIRST and verdict.label == MENTION)


def conclusion(state: Scan) -> list[str]:
    """What comes after the lines written as they were found."""
    if state.mode == ALL:
        lines = _timeline(state)
    else:
        lines = _first(state)
    return [*lines, coverage(state)]


def _timeline(state: Scan) -> list[str]:
    if not state.yes:
        return ["No day checked shows it."]
    ordered = sorted(
        state.yes, key=lambda v: (v.excerpt.date is None, v.excerpt.date or "")
    )
    if ordered == state.yes:  # read in that order: as good as written already
        return []
    return ["", "In date order:", *(line(state, v) for v in ordered)]


def oldest(verdicts: list[Verdict]) -> Verdict | None:
    dated = sorted(
        (v for v in verdicts if v.excerpt.date), key=lambda v: v.excerpt.date or ""
    )
    return dated[0] if dated else None


def first_day(state: Scan) -> Verdict | None:
    """The first time: the oldest day that says it in the question's words, else the
    oldest the model said yes of."""
    return oldest([v for v in state.yes if state.says_it(v)]) or oldest(state.yes)


def _first(state: Scan) -> list[str]:
    def before(verdict: Verdict | None, day: Verdict | None) -> bool:
        if verdict is None:
            return False
        return day is None or (verdict.excerpt.date or "") < (day.excerpt.date or "")

    first = first_day(state)
    earliest = oldest(state.mentions)
    earlier = oldest(state.yes)
    lines = [""]
    if before(earliest, first):
        lines.append(f"Earliest mention: {_cite(earliest)}")
    if before(earlier, first):
        lines.append(f"Possibly earlier, in other words: {_cite(earlier)}")
    if first:
        lines.append(f"First in the days checked: {_cite(first)}")
    elif not state.yes:
        lines.append("No day checked shows it.")
    return lines


def _cite(verdict: Verdict | None) -> str:
    assert verdict is not None
    return f"{verdict.excerpt.date} [{verdict.number}]"


def coverage(state: Scan) -> str:
    """How much was read, for a partial answer not to pass for a complete one."""
    total = state.found or len(state.excerpts)
    many = f"{total}+" if total >= SEARCH_DAYS else str(total)
    order = "best first" if state.mode == ALL else "oldest first"
    if state.checked == total:
        said = f"Checked all {many} days found"
    else:
        said = f"Checked {state.checked} of {many} days found, {order}"
        if state.stopped != "found":
            said += ": there may be more in the others"
    undated = sum(e.date is None for e in state.excerpts[state.checked :])
    if undated:
        said += f"; {undated} without a date not checked"
    silent = state.checked - len(state.verdicts)
    if silent > 0:
        said += f"; no verdict on {silent}"
    return said + "."
