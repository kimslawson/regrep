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
Or run it in [timeline mode](#timeline-mode) to see exactly when a phrase
appeared on a page and when it disappeared.

## Installation

Requires Python 3.10+.

```bash
# From PyPI (v0.3.0+)
pipx install regrep          # or: uvx regrep 'pattern' https://example.com

# From source
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

# Timeline mode: when did a phrase appear and vanish?
regrep --timeline 'deprecated_function\(\)' https://developer.example.com/docs

# Search a different archive
regrep --provider archivetoday 'headline text' https://news.example.com/story

# Machine-readable output for scripting
regrep --json 'needle' https://example.com | jq .replay_url
```

### Timeline mode

`--timeline` answers a different question than plain search: not *"show me
every match"* but *"when did this text appear, and when did it go away?"* It
walks the snapshots in order, tracks whether the pattern is present in each,
and collapses that into a chronology of appearances and disappearances:

```console
$ regrep --timeline 'deprecated_function\(\)' https://developer.example.com/docs
http://developer.example.com/docs
- absent   2018-07-02 00:00:00   (1 snapshot)
    absent at the earliest snapshot (2018-07-02 00:00:00)
+ present  2019-03-14 07:22:41 → 2020-06-01 00:00:00   (2 snapshots)
    appeared between 2018-07-02 00:00:00 (absent) and 2019-03-14 07:22:41
    1:<p>Migration note: deprecated_function() will be removed in v2.</p>
- absent   2021-06-02 18:05:19   (1 snapshot)
    vanished between 2020-06-01 00:00:00 (present) and 2021-06-02 18:05:19
+ present  2023-01-01 00:00:00   (1 snapshot)
    appeared between 2021-06-02 18:05:19 (absent) and 2023-01-01 00:00:00
    1:<p>the old deprecated_function() note is back for reference</p>
  → present as of 2023-01-01 00:00:00, the most recent snapshot checked
```

A change is *bracketed* rather than pinpointed — the archive rarely captures
the exact moment something changed, so regrep reports the interval between
the last snapshot without the phrase and the first one with it. In timeline
mode regrep deliberately keeps duplicate captures (which plain search
collapses) so a phrase that vanishes and later returns is detected. Exit code
is `0` if the pattern was ever present, `1` if never. `--timeline --json`
emits one `timeline-segment` object per run.

### Options

| Flag | Description |
| --- | --- |
| `-i`, `--ignore-case` | Case-insensitive matching (matching is case-sensitive by default, like grep). |
| `-F`, `--fixed-strings` | Treat `PATTERN` as a literal string, not a regex. |
| `--visible` | Search only human-visible text: tags, scripts, styles, comments, and head metadata are stripped first. Line numbers then refer to the extracted text, not the HTML source. |
| `--timeline` | Report when the pattern appeared and vanished across snapshots instead of listing every match (see [Timeline mode](#timeline-mode)). Exit `0` if ever present. |
| `--from DATE`, `--to DATE` | Only consider snapshots in this range. Accepts `YYYY`, `YYYY-MM`, `YYYY-MM-DD`, or a full 14-digit timestamp. |
| `-l`, `--limit N` | Consider at most N snapshots (default 100, `0` = no limit). |
| `--daily` / `--monthly` / `--yearly` | At most one snapshot per day/month/year. |
| `--prefix` | Treat `URL` as a prefix and search everything archived under it (Wayback only). |
| `--provider NAME` | Archive to search: `wayback` (default) or `archivetoday`. See [Providers](#providers). |
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

## Providers

| Provider | Backend | Notes |
| --- | --- | --- |
| `wayback` (default) | Internet Archive [CDX API](https://archive.org/developers/wayback-cdx-server.html) + `id_` raw fetch | Content digests (dedupe), server-side date/limit/collapse filtering, prefix search, and pristine original bytes. |
| `archivetoday` | [archive.today](https://archive.today) via its [Memento](https://datatracker.ietf.org/doc/html/rfc7089) TimeMap | No digests, no raw-bytes endpoint (fetched pages include archive.today's wrapper — `--visible` helps), date/limit/collapse applied client-side, no `--prefix`. archive.today is bot-hostile (Cloudflare/CAPTCHAs), so expect the occasional blocked request, which regrep reports rather than hides. |

```bash
regrep --provider archivetoday 'quoted phrase' https://example.com/article
```

Both providers feed the same engine, so `--visible`, `--timeline`, `--json`,
date ranges, and coloring all work identically regardless of archive.

## Architecture: built for more than one archive

Everything above the network is provider-agnostic:

```
cli.py           argument parsing, exit codes
core.py          dedupe → concurrent fetch → match → chronological order
extract.py       --visible text extraction
timeline.py      appeared/vanished segments for --timeline
output.py        terminal/JSON rendering
providers/
  base.py        Snapshot + SnapshotProvider seam; shared HTTP (session, stream_text)
  wayback.py     CDX index + id_ fetch
  memento.py     RFC 7089 TimeMap parser + provider (reusable Memento base)
  archivetoday.py  archive.today endpoint on top of memento.py
```

A provider implements two methods — `list_snapshots(url, ...)` and
`fetch(snapshot)` — and registers itself in `providers/__init__.py`; the
`--provider` flag picks it up automatically. The Memento base
(`memento.py`) means the *next* [RFC 7089](https://datatracker.ietf.org/doc/html/rfc7089)
archive — the TimeTravel aggregator, a national/academic web archive — is a
few lines pointing at a different TimeMap endpoint.

Self-hosted archives already work today: point
`REGREP_WAYBACK_CDX_URL` / `REGREP_WAYBACK_WEB_URL` at any CDX-compatible
server (e.g. [pywb](https://github.com/webrecorder/pywb)), or
`REGREP_ARCHIVETODAY_TIMEMAP_URL` at any Memento TimeMap endpoint.

## Roadmap

- [x] Timeline / diff mode: when a phrase appeared and disappeared across snapshots (`--timeline`)
- [x] PyPI release wired up (trusted-publisher auto-publish on version tags)
- [x] archive.today provider (via a reusable Memento TimeMap base)
- [ ] Memento TimeTravel aggregator (`--provider timetravel`) — now a short hop on `memento.py`
- [ ] Local snapshot caching (`requests-cache`/SQLite) so repeat searches are instant — deliberately deferred for now
- [ ] `-v`/`--invert-match`, `-C`/`--context` (the short `-v` is reserved for this — grep users' fingers expect it)
- [ ] `--newest` to scan most recent snapshots first
- [ ] Multiple URLs / a URL list on stdin

## Development

```bash
pip install -e '.[dev]'
pytest          # offline test suite (all network is mocked)
ruff check .
```

`docs/DESIGN.md` records the design decisions and the review of the original
prototype this grew from.

### Releasing

Releases publish to PyPI via [trusted publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC — no API token in the repo). One-time setup: on PyPI, add a pending
publisher for project `regrep`, repo `kimslawson/regrep`, workflow
`release.yml`, environment `pypi`. Then to cut a release, bump the version in
both `pyproject.toml` and `src/regrep/__init__.py`, commit, and push a
matching tag:

```bash
git tag v0.3.0 && git push origin v0.3.0
```

`.github/workflows/release.yml` verifies the tag matches the package version,
builds the sdist + wheel, and publishes.

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
