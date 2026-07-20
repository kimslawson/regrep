# regrep 🔍🌐

`regrep` greps the *history* of the web: it searches archived snapshots of a
URL for a regular expression, right from your terminal.

```console
$ regrep --visible 'deprecated_function\(\)' https://developer.example.com/docs
2019-03-14 07:22:41  https://developer.example.com/docs
https://web.archive.org/web/20190314072241/https://developer.example.com/docs
88:Migration note: deprecated_function() will be removed in v2.

2021-06-02 18:05:19  https://developer.example.com/docs
https://web.archive.org/web/20210602180519/https://developer.example.com/docs
91:deprecated_function() has been removed. Use replacement_function().
```

### The problem

Sometimes you search for something and a search engine surfaces a matching
result — but by the time you visit the page, it has changed or vanished.
There used to be a "Cached" link for exactly this moment; Google retired it
in early 2024. The information usually still exists in web archives, but
digging through snapshots by hand is miserable.

### The solution

`regrep` queries the Wayback Machine's CDX index for snapshots of a URL,
fetches their original content concurrently (politely), and greps each one —
with output, colors, and exit codes that feel like the tools you already use.

## Installation

Requires Python 3.10+.

```bash
# Run without installing
uvx --from git+https://github.com/kimslawson/regrep regrep 'pattern' https://example.com

# Or install
pipx install git+https://github.com/kimslawson/regrep
# ... or, from a clone:
pip install .
```

Dependencies: `requests`, `beautifulsoup4`.

## Usage

```
regrep [OPTIONS] PATTERN URL
```

```bash
# Search raw HTML source across a page's history
regrep 'deprecated_api_v1' https://developer.example.com/docs

# Search only the text a reader would have seen
regrep --visible 'Rustacean' https://www.rust-lang.org

# Constrain the era, one snapshot per month
regrep --from 2023-01 --to 2024-06 --monthly 'pricing' https://example.com/plans

# Literal string, case-insensitive, everything under a path
regrep -i -F '$99' --prefix https://example.com/plans

# Machine-readable output for scripting
regrep --json 'needle' https://example.com | jq .replay_url
```

### Options

| Flag | Description |
| --- | --- |
| `-i`, `--ignore-case` | Case-insensitive matching (matching is case-sensitive by default, like grep). |
| `-F`, `--fixed-strings` | Treat `PATTERN` as a literal string, not a regex. |
| `--visible` | Search only human-visible text: tags, scripts, styles, comments, and head metadata are stripped first. Line numbers then refer to the extracted text, not the HTML source. |
| `--from DATE`, `--to DATE` | Only consider snapshots in this range. Accepts `YYYY`, `YYYY-MM`, `YYYY-MM-DD`, or a full 14-digit timestamp. |
| `-l`, `--limit N` | Consider at most N snapshots (default 100, `0` = no limit). |
| `--daily` / `--monthly` / `--yearly` | At most one snapshot per day/month/year. |
| `--prefix` | Treat `URL` as a prefix and search everything archived under it. |
| `--provider NAME` | Archive to search (default: `wayback`). |
| `-w`, `--workers N` | Concurrent fetches (default 5 — be kind, the archive is a shared resource). |
| `--timeout SECONDS` | Per-snapshot read timeout (default 30). |
| `--max-size MB` | Skip snapshots larger than this (default 10). |
| `--max-columns N` | Truncate displayed lines to a window of N characters around the match (default 200, `0` = never). |
| `--color WHEN` | `auto` (default), `always`, or `never`. `auto` colors only real terminals and honors [`NO_COLOR`](https://no-color.org/). |
| `--json` | Emit one JSON object per matching line (NDJSON). |
| `-q`, `--quiet` | Suppress progress and summary messages on stderr. |

### Exit codes

Same contract as grep: `0` — at least one match; `1` — no matches (or no
snapshots); `2` — error (bad arguments, invalid regex, archive index
unreachable). `130` on Ctrl-C.

Matches go to **stdout**; progress and the end-of-run summary go to
**stderr**, so piping stays clean.

## How it works

1. **Index** — the [CDX API](https://archive.org/developers/wayback-cdx-server.html)
   lists captures of the URL (successful ones only), collapsing captures
   whose content digest is identical, so unchanged pages aren't fetched
   twice. regrep also skips duplicate digests that aren't adjacent.
2. **Fetch** — each snapshot is downloaded through the `id_` endpoint, which
   returns the original bytes without the Wayback toolbar or URL rewriting
   that would otherwise pollute matches. Fetches run in a small thread pool
   with retries, backoff, and `Retry-After` support for `429`/`5xx`.
3. **Match** — Python regex, line by line, ripgrep-style output: datestamp
   and source in green, line numbers in blue, matches in red.

Character encodings are detected when snapshots don't declare one (old pages
lie constantly), binary content is detected and skipped, and oversized
snapshots are capped rather than buffered forever.

## Architecture: built for more than one archive

The Wayback Machine is the first backend, not the only one. Everything above
the network is provider-agnostic:

```
cli.py        argument parsing, exit codes
core.py       dedupe → concurrent fetch → match → chronological order
extract.py    --visible text extraction
output.py     terminal/JSON rendering
providers/
  base.py     Snapshot + SnapshotProvider interface (the seam)
  wayback.py  CDX index + id_ fetch implementation
```

A provider implements two methods — `list_snapshots(url, ...)` and
`fetch(snapshot)` — and registers itself in `providers/__init__.py`; the
`--provider` flag picks it up automatically. Planned next, per
[RFC 7089 (Memento)](https://datatracker.ietf.org/doc/html/rfc7089):
archive.today, the Memento TimeTravel aggregator, and national/academic web
archives.

Self-hosted archives already work today: point
`REGREP_WAYBACK_CDX_URL` / `REGREP_WAYBACK_WEB_URL` at any CDX-compatible
server (e.g. [pywb](https://github.com/webrecorder/pywb)).

## Roadmap

- [ ] Local snapshot caching (`requests-cache`/SQLite) so repeat searches are instant — deliberately deferred for now
- [ ] More Memento providers: archive.today, TimeTravel aggregator
- [ ] `-v`/`--invert-match`, `-C`/`--context` (the short `-v` is reserved for this — grep users' fingers expect it)
- [ ] `--newest` to scan most recent snapshots first
- [ ] Diff mode: show when a phrase appeared/disappeared between snapshots
- [ ] PyPI release (the name `regrep` is unclaimed as of 2026-07)

## Development

```bash
pip install -e '.[dev]'
pytest          # offline test suite (all network is mocked)
ruff check .
```

`docs/DESIGN.md` records the design decisions and the review of the original
prototype this grew from.

## Inspiration & acknowledgments

`regrep` owes its aesthetic, output formatting, and core spirit to
[ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) by Andrew Gallant.
While ripgrep revolutionized how we search the local file system with
unmatched speed and beautiful terminal design, regrep attempts to bring a
tiny sliver of that user experience to the historical timeline of the World
Wide Web. We owe a massive debt of gratitude to the ripgrep project for
setting the gold standard of what a modern CLI search tool should look and
feel like.

And none of this works without the
[Internet Archive](https://archive.org/) keeping the web's memory alive —
[consider donating](https://archive.org/donate).

## License

MIT — see [LICENSE](LICENSE).
