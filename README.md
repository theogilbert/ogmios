# ogmios

Questions about your markdown notes, answered by a small local LLM from the days it
finds, in English or French, whatever the language you wrote them in.

```
$ ogmios ask "pourquoi le DAG partner_files_ingest a échoué en janvier 2023 ?"
ogmios: reading 3 excerpts, 2023-01-03 to 2023-01-09
À cause d'une rotation de la clé SSH côté partenaire non communiquée [2].

[2] 2023-01-05 │ ~/work/done.md:44
```

Everything runs on the CPU. No server, no GPU, nothing in memory between questions.

| | |
|---|---|
| Models on disk | 135 MB (embeddings) and 3.3 GB (LLM), downloaded by `ogmios setup` from Hugging Face (pinned revisions), then never fetched again |
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
- **Search.** The days to read are found by keyword search (SQLite FTS5,
  accent-insensitive; `NET-881`-like jargon is matched as a phrase) and semantic search
  (`multilingual-e5-small`, cross-lingual), merged with reciprocal rank fusion and
  grouped by day. Time expressions in English or French ("yesterday", "last week", "il y
  a deux semaines", "in March 2024", "14 mars") restrict it to that period; asked with no
  topic, as in "what did I do last week?", the period's days are read in full.
- **Embedding model.** Run with ONNX Runtime directly. Loading the tokenizer is most of
  its load time; its 250k-word multilingual vocabulary is pruned once to the Latin script,
  which tokenizes French and English text exactly as before at a third of the cost. ONNX
  Runtime's usage telemetry is turned off (`ORT_DISABLE_TELEMETRY`).
- **Answer.** The best days (up to `-n/--days`, 8 by default, ~2.5k tokens) are handed to
  [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf), run
  in-process with llama.cpp, told to answer only from them, cite them, and say when they
  do not hold the answer. Its file is memory-mapped, so it loads in about a second; most
  of the wait is the model reading the notes, at ~170 tokens/s on a 6-core laptop CPU.
- **Freshness.** `ask` re-indexes first when a file changed. Only new or edited text is
  re-embedded, so this is near-instant after the first run.

## Install

```sh
uv tool install --editable ~/projects/ogmios  # compiles llama.cpp: needs a C++ compiler and cmake, ~3 min
ogmios setup             # downloads the models (3.4 GB), writes them to the config
```

Nothing is downloaded behind your back: a command whose model is missing fails and
points at `ogmios setup`, which says what it downloads and where before doing it. An
interrupted download resumes where it stopped on the next run.

## Configure

From the command line:

```sh
ogmios config add ~/work/done.md                 # a file of daily notes
ogmios config add ~/notes --exclude "archive/**" # every **/*.md under a directory
ogmios config add ~/journal.md --month-first --delimiter '^## \d{4}'
ogmios config remove ~/notes
ogmios config model e5-small                     # show or set the embedding model
                                                 # (then `ogmios setup` installs it)
ogmios config show                               # print the file
ogmios config edit                               # open it in $EDITOR
```

These edit `~/.config/ogmios/config.toml` in place, keeping its comments. By hand:

```toml
# model = "e5-small-int8"   # or "e5-small" (fp32, 470 MB, same results, slower)
#                           # or "e5-base-int8" (280 MB, ~40% slower, ~600 MB peak)
# llm = "gemma-4-e2b"

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

Check how a file is split before trusting the results:

```sh
ogmios inspect ~/work/done.md            # sections and blocks
ogmios inspect ~/work/done.md --chunks   # and what gets embedded
```

## Use

```sh
ogmios index                         # first run: embeds everything (ask also does it)
ogmios ask "..."
ogmios ask --prompt "..."            # what the model would be given, without running it
```

A 2B model reading excerpts can still misread them, or miss one of two answers: check
the cited lines, printed after the answer as `[n] day │ path:line`.

`ogmios status` shows where the config, index and models are stored, what each source
matches, and whether the index is up to date.

`-s/--source PATH` reads a file or directory instead of the configured sources.
`OGMIOS_CONFIG` and `OGMIOS_DB` override the config and index locations.

## Development

```sh
uv sync --group dev
make ci                                  # ruff, ty, pytest
uv run python scripts/eval.py            # retrieval benchmark, per embedding model
```
