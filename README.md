# ogmios

Questions about your markdown notes, answered by a small local LLM from the days it
finds, in English or French, whatever the language you wrote them in.

```
$ ogmios ask "pourquoi le DAG partner_files_ingest a échoué en janvier 2023 ?"
ogmios: reading 3 excerpts, 2023-01-03 to 2023-01-09
À cause d'une rotation de la clé SSH côté partenaire non communiquée [2].

[2] 2023-01-05 | ~/work/done.md:44
```

Everything runs on the CPU. No server, no GPU, nothing in memory between questions.

| | |
|---|---|
| Models on disk | 135 MB (embeddings) and 3.3 GB (LLM), downloaded by `ogmios install` from Hugging Face (pinned revisions), then never fetched again |
| First full index, ~11k lines | ~30 s, ~750 MB peak |
| `ask` | ~5–15 s, ~1.5 GB plus the LLM file mapped from disk, then the process exits |

## How it works

- **Splitting.** A daily-notes file is split on its date headers, detected automatically in
  English and French and in any mix of formats (`## 2024-05-14`, `Mardi 3 janvier 2023`,
  `**March 6, 2024**`, `12/03/2023`, ...). Missing years are inferred from neighbouring
  entries. Files without dates are split on their headings instead.
- **Chunks.** Each day is indexed at three overlapping granularities: the whole day, each
  top-level block (a `> project` or `- project` with what is indented under it), and each
  pair of adjacent blocks, so that a topic running across two items is still found. Chunks
  carry their date as context.
- **Other wordings.** A note may say it in other words, or in the other language. The
  model first writes the question's key terms as a note would word them, in English and
  in French, with synonyms (~2 s; `--literal` skips it). Both the question and the
  question with those terms are searched, and the two rankings merged, so that the terms
  add days without pushing down those the question finds by itself.
- **Search.** The days to read are found by keyword search (SQLite FTS5,
  accent-insensitive; `NET-881`-like jargon is matched as a phrase) and semantic search
  (`multilingual-e5-small`, cross-lingual), merged with reciprocal rank fusion and
  grouped by day. Time expressions in English or French ("yesterday", "last week", "il y
  a deux semaines", "in March 2024", "14 mars", "between March and May 2024", "depuis
  septembre", "la semaine du 14 mars", "Q1 2024", "l'été dernier", "last weekend")
  restrict it to that period; asked with no
  topic, as in "what did I do last week?", the period's days are read in full.
- **Embedding model.** Run with ONNX Runtime directly. Loading the tokenizer is most of
  its load time; its 250k-word multilingual vocabulary is pruned once to the Latin script,
  which tokenizes French and English text exactly as before at a third of the cost. ONNX
  Runtime's usage telemetry is turned off (`ORT_DISABLE_TELEMETRY`).
- **Excerpts.** Of the best days (up to `-n/--days`, 20 by default), the model is given
  6000 characters (~2.5k tokens): first the item that matched best in each day, for as
  many days as fit in 60% of that, then more of those days, an item at a time and round
  after round: their other items that matched, then the items around them. Lines left
  out are marked `…`. Asked about a period with no topic, it gets the period's days whole.
- **Answer.** The excerpts are handed to
  [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf), run
  in-process with llama.cpp, told to answer only from them, mention every excerpt that
  answers, cite them at the end of each sentence, and say when they do not hold the
  answer. Its file is memory-mapped, so it loads in about a second; most
  of the wait is the model reading the notes, at ~170 tokens/s on a 6-core laptop CPU.
- **Freshness.** `ask` re-indexes first when a file changed. Only new or edited text is
  re-embedded, so this is near-instant after the first run.

## Install

```sh
uv tool install --editable ~/projects/ogmios  # compiles llama.cpp: needs a C++ compiler and cmake, ~3 min
ogmios install           # downloads the models (3.4 GB), writes them to the config
```

Nothing is downloaded behind your back: a command whose model is missing fails and
points at `ogmios install`, which says what it downloads and where before doing it. An
interrupted download resumes where it stopped on the next run.

## Configure

From the command line:

```sh
ogmios config add ~/work/done.md                 # a file of daily notes
ogmios config add ~/notes --exclude "archive/**" # every **/*.md under a directory
ogmios config add ~/journal.md --month-first --delimiter '^## \d{4}'
ogmios config remove ~/notes
ogmios config model e5-small                     # show or set the embedding model
ogmios config llm REPO/FILE.gguf                 # show or set the LLM (see below)
                                                 # (then `ogmios install` installs them)
ogmios config show                               # print the file
ogmios config edit                               # open it in $EDITOR
```

These edit `~/.config/ogmios/config.toml` in place, keeping its comments. By hand:

```toml
# model = "e5-small-int8"   # or "e5-small" (fp32, 470 MB, same results, slower)
#                           # or "e5-base-int8" (280 MB, ~40% slower, ~600 MB peak)
# llm = "gemma-4-e2b"       # or any GGUF file on Hugging Face (see below)

[[sources]]
path = "~/work/done.md"
# split = "auto"                         # auto | dates | headings
# delimiters = ['^## \d{4}-\d{2}-\d{2}'] # if auto-detection gets it wrong
# day_first = true                       # 03/04/2024 is the 3rd of April

[[sources]]
path = "~/notes"
glob = "**/*.md"
exclude = ["archive/**"]
```

### Another LLM

The LLM can be any GGUF file on a public Hugging Face repository, without changing the
code: the model's chat template comes with the file. Give it as `REPO/FILE.gguf`, or as
the file's URL, copied from the browser:

```sh
ogmios install --llm google/gemma-4-E4B-it-qat-q4_0-gguf/gemma-4-E4B_q4_0-it.gguf
ogmios install --llm https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf/blob/main/gemma-4-E4B_q4_0-it.gguf
ogmios install --llm gemma-4-e2b         # back to the built-in one
```

`install` downloads the commit the repository is at, and writes it into the config
(`REPO/FILE.gguf@REVISION`): the model then stays the same until you change it. A new
architecture may need a newer llama.cpp (`uv tool upgrade ogmios --reinstall`). The
prompts were tuned on Gemma 4 E2B: check another model's answers, and the language they
come in, on a few questions you know the answer to.

Check how a file is split before trusting the results:

```sh
ogmios inspect ~/work/done.md            # sections and blocks
ogmios inspect ~/work/done.md --chunks   # and what gets embedded
```

## Use

```sh
ogmios index                         # first run: embeds everything (ask also does it)
ogmios ask "..."
ogmios ask --prompt "..."            # what the model would be given, without the answer
ogmios ask --literal "..."           # the question's own words only, ~2 s faster
ogmios ask --json "..."              # one JSON object, for editor plugins
```

On a terminal, a status line with a spinner shows what is going on (indexing, loading the
model, thinking of other wordings, searching, reading the notes) until the answer starts
to appear.

A date in the answer that is neither the day of an excerpt nor written in one gets a
warning on stderr: a small model sometimes writes a wrong date while citing the right
excerpt.

`--json` prints, once the answer is written, `{"question", "wordings", "answer",
"warnings", "excerpts"}`, where `wordings` is what was searched besides the question, and
each excerpt the model read is `{"n", "date", "title", "path", "line", "cited"}`: `n` is
the number the answer cites it by, `date` is null for a section without one, and `line`
is 1-based. Progress goes to stderr; on failure, stdout is empty, the message is on
stderr and the exit status is non-zero.

The days the answer cites are printed after it, as `[n] day | path:line`.

`ogmios status` shows where the config, index and models are stored, what each source
matches, and whether the index is up to date.

`-s/--source PATH` reads a file or directory instead of the configured sources.
`OGMIOS_CONFIG` and `OGMIOS_DB` override the config and index locations.

## Getting good answers

The model only ever reads what the search hands it: about 2.5k tokens of excerpts, out of
notes that may hold far more. Most bad answers come from the search missing the right
day, not from the model; the rest from the model misreading what it was given.

### In general

- **Ask about your notes,** not about the world: the model is told to answer only from
  the excerpts, and says so when they do not hold the answer.
- **Use the words your notes use** when you know them: ticket ids, hostnames, project and
  tool names. The keyword search matches them exactly, which is more reliable than
  meaning alone. When you do not know them, describe the thing plainly: other wordings,
  in English and French, are searched too (unless `--literal`).
- **Say when, if you know.** A time expression ("in 2022", "depuis septembre", "la
  semaine du 14 mars", "Q1 2024", "last summer") narrows the search to that period before
  anything is read, so that other years do not compete. It is shown on stderr: check it
  is the period you meant.
- **One question at a time.** For something that happened several times, ask for all of
  them: "list every time I…" rather than "when did I…".
- **Summaries of a period** ("what did I do last week?") read the period's days whole,
  most recent first, until the budget is spent: a week reads in full, a year does not.
  Ask about a topic over a long period instead ("what did I do on the k8s migration in
  2024?").
- **Check the sources.** Each claim should end with the number of an excerpt; the cited
  days are listed under the answer. An answer with nothing cited rests on nothing. A
  date flagged on stderr is not in the notes read: the day in the source line is the
  right one.
- **When an answer looks wrong, run it again with `--prompt`.** If the day that answers
  is not among the excerpts, the search missed it: rephrase with the words of the note,
  add a time frame, or name the ticket or project. If it is there, the model misread it:
  ask more narrowly, or try a larger model (see [Another LLM](#another-llm)).
- **`--literal`** saves ~2 s when you know the exact words; leave it out when unsure of
  the wording, which is when other wordings matter.
- **Notes that are easy to search:** dated headers that `ogmios inspect` splits right,
  one item per topic, and the names of things (tickets, hosts, projects) written out.

### With Gemma 4 E2B, the default LLM

What it does well and less well, seen while building ogmios:

- **It answers in the language of the question,** English or French, whatever the
  language of the notes. Ask in the language you want the answer in; other languages are
  not supported.
- **It tends to give one answer where there are several,** even when the excerpts hold
  both and it is told to mention them all: "when were the SFTP flows validated?" got the
  2024 validation, not the 2022 one sitting next to it. Ask "list every time…", and look
  at the other excerpts with `--prompt` when it matters.
- **It can misquote a date** while citing the right excerpt (2024-03-15 for a note of the
  14th). Such dates are flagged on stderr; the source line's day is the right one.
- **Do not ask it to compute with dates** ("how many weeks between…", "which weekday
  was…"): small models are poor at it. Read the days off the sources instead.
- **It does not always know the jargon for a description:** "tâche d'interrogation
  périodique" was not linked to a "polling job". Use the technical term when you know it.
- **It picks one reading of an ambiguous question:** "when did I move to Airflow?" got
  the move to Airflow on k8s, not the first use of Airflow. Say which one you mean.
- **It is honest about absence:** asked about something the notes never mention, it says
  so rather than inventing an answer.
- **Speed:** ~2 s for other wordings, then ~170 tokens/s reading the notes and ~15
  tokens/s writing: 10 to 15 s for most questions on a laptop CPU.

## Development

```sh
uv sync --group dev
make ci                                  # ruff, ty, pytest
uv run python scripts/eval.py            # retrieval benchmark, per embedding model
```
