# ogmios

Local search over markdown notes, for when you remember the idea but not the words you
used, or the language you wrote it in.

```
$ ogmios search "when did I request network flows between the sftp server and airflow?"
~/work/done.md:19:1:2022-04-04 │ - Ouverture de flux demandée entre srv-sftp-part01 et le cluster Airflow, ticket RESEAU-4512
~/work/done.md:94:1:2024-03-14 │ Asked the network team to open flows from the new k8s cluster to the partner SFTP
...
```

Everything runs on the CPU. No server, no GPU, nothing in memory between searches.

| | |
|---|---|
| Model on disk | 135 MB, downloaded once from Hugging Face (pinned revision), then never fetched again |
| First full index, ~11k lines | ~30 s, ~750 MB peak |
| Search | ~0.6 s, ~420 MB peak, then the process exits |
| `--keyword` search | ~0.1 s, ~40 MB |

## How it works

- **Splitting.** A daily-notes file is split on its date headers, detected automatically in
  English and French and in any mix of formats (`## 2024-05-14`, `Mardi 3 janvier 2023`,
  `**March 6, 2024**`, `12/03/2023`, ...). Missing years are inferred from neighbouring
  entries. Files without dates are split on their headings instead.
- **Chunks.** Each day is indexed at three overlapping granularities: the whole day, each
  top-level block (a `> project` or `- project` with what is indented under it), and each
  pair of adjacent blocks, so that a topic running across two items is still found. Chunks
  carry their date as context.
- **Search.** Keyword search (SQLite FTS5, accent-insensitive; `NET-881`-like jargon is
  matched as a phrase) and semantic search (`multilingual-e5-small`, cross-lingual) are
  merged with reciprocal rank fusion. Results are grouped by day, one line each, pointing
  at the best matching line.
- **Model.** Run with ONNX Runtime directly. Loading the tokenizer is most of a search's
  time; its 250k-word multilingual vocabulary is pruned once to the Latin script, which
  tokenizes French and English text exactly as before at a third of the cost. ONNX
  Runtime's usage telemetry is turned off (`ORT_DISABLE_TELEMETRY`).
- **Freshness.** `search` re-indexes first when a file changed. Only new or edited text is
  re-embedded, so this is near-instant after the first run.

## Install

```sh
uv tool install --editable ~/projects/ogmios
```

## Configure

From the command line:

```sh
ogmios config add ~/work/done.md                 # a file of daily notes
ogmios config add ~/notes --exclude "archive/**" # every **/*.md under a directory
ogmios config add ~/journal.md --month-first --delimiter '^## \d{4}'
ogmios config remove ~/notes
ogmios config model e5-small                     # show or set the embedding model
ogmios config show                               # print the file
ogmios config edit                               # open it in $EDITOR
```

These edit `~/.config/ogmios/config.toml` in place, keeping its comments. By hand:

```toml
# model = "e5-small-int8"   # or "e5-small" (fp32, 470 MB, same results, slower)

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
ogmios index                         # first run: downloads the model, embeds everything
ogmios search "..."                  # best match first
ogmios search --sort date "..."      # the same hits, as a timeline
ogmios search --keyword "NET-881"    # keywords only, no model load (~0.1s)
ogmios search --json "..."           # one JSON object per line
```

Time expressions in English or French ("yesterday", "last week", "il y a deux semaines",
"in March 2024", "14 mars") restrict the search to that period, shown on stderr. Asked
with no topic, as in "what did I do yesterday", the period's notes are listed in order,
or nothing if there are none.

`ogmios status` shows where the config, index and model are stored, what each source
matches, and whether the index is up to date.

`-s/--source PATH` searches a file or directory instead of the configured sources.
`OGMIOS_CONFIG` and `OGMIOS_DB` override the config and index locations.

The output is grep-like (`path:line:col:text`), so any picker can open the results. In
Neovim: `utilities.search.notes()` (fzf-lua).

## Development

```sh
uv sync --group dev
make ci                                  # ruff, ty, pytest
uv run python scripts/eval.py            # retrieval benchmark, per model
```
