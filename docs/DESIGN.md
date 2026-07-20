# regrep design notes

This document records where regrep came from, what was wrong with the
original prototype, and the decisions baked into the current architecture —
so future changes (new providers, caching, invert-match, ...) can be made
without re-deriving the reasoning.

## Origin

regrep started as a chat-built prototype (single-file `regrep.py`): search
Wayback Machine snapshots of a URL for a regex, with concurrent fetching, a
`--visible` text-extraction mode, and ripgrep-style colors. That prototype
proved the concept; this codebase is its productionization. The core idea is
unchanged: **CDX index → fetch raw snapshots (`id_`) → regex per line →
ripgrep-flavored output** (matches red, source/datestamp green, line numbers
blue).

## Prototype review: what was fixed and why

Issues found while reviewing/breaking the prototype, roughly in order of
severity:

1. **Silent case-insensitivity.** `re.IGNORECASE` was hardcoded, so `regrep
   Needle` and `regrep needle` were indistinguishable — surprising for a
   grep. Now case-sensitive by default with `-i`, like grep/rg.
2. **`-v` squatting.** `-v` meant `--visible`, but every grep user's fingers
   know `-v` as invert-match. `--visible` is now long-form only; `-v` stays
   reserved.
3. **ANSI codes emitted unconditionally**, corrupting piped/redirected
   output. Now `--color auto|always|never` with tty detection, `NO_COLOR`,
   and `TERM=dumb` support.
4. **Nondeterministic output order.** Results printed in thread-completion
   order, so consecutive runs shuffled the timeline. Results are now always
   chronological.
5. **Highlighting via `regex.sub()`** could inject color codes at every
   position for zero-width-capable patterns (`x*`), and the earliest version
   used `re.findall`, which returns *group* contents, not matches, for
   patterns with capturing groups. Matching now records `finditer` spans and
   the renderer paints those spans; zero-width spans paint nothing.
6. **Fetching the wrong URL.** The CDX query returned only timestamps, and
   snapshot URLs were rebuilt from the *user's* URL string; when CDX matched
   a canonicalized variant (scheme/host/query differences) the fetch and the
   report could disagree. Each CDX row's `original` field is now used.
7. **No politeness.** A new connection per request, no retries, no
   `Retry-After` handling, no User-Agent, default 10 workers. Now: pooled
   per-thread sessions, retry/backoff on 429/502/503/504, an identifying
   User-Agent, default 5 workers (hard cap 32).
8. **Duplicate work.** Snapshots of an unchanged page were fetched again and
   again. Now `collapse=digest` server-side plus a client-side seen-digest
   set for non-adjacent duplicates.
9. **Unbounded memory / garbage input.** No size cap (a single huge snapshot
   could buffer forever), no binary detection (grepping JPEG bytes), and
   `response.text` trusting requests' ISO-8859-1 default for `text/*`
   without a declared charset (mangles old UTF-8 pages). Now: streamed
   download with a `--max-size` cap, known-binary mimetypes skipped before
   fetching, NUL-byte backstop after, and charset detection when none is
   declared.
10. **Errors swallowed silently** (`except: pass`), and no meaningful exit
    status. Now every snapshot lands in a stats bucket (scanned / matched /
    duplicate / binary / non-text / too-large / error) reported on stderr,
    with grep's exit-code contract (0 match, 1 no match, 2 error).
11. **`--visible` incompleteness.** Only `script`/`style`/`meta` were
    stripped (plus a no-op `"[document]"`); comments, `noscript`,
    `template`, `iframe`, and `head` content leaked into "visible" text.
12. **Truncation at a flat 100 chars** cut long lines *before* the match
    position — a match at column 200 of minified HTML printed as an ellipsis
    with no match visible. Now `--max-columns` renders a window *around* the
    first match.
13. **Missing range selection.** `--from/--to` appeared in the concept but
    were never implemented; done, plus `--daily/--monthly/--yearly`
    collapsing and `--prefix`.
14. **HTTP for the CDX endpoint**; now HTTPS.
15. **`BrokenPipeError` traceback** when piping into `head`; now exits 141
    quietly.

## Decisions of record

- **grep's contract wins ties.** Flags, exit codes, stdout/stderr split, and
  case behavior follow grep/ripgrep conventions unless there's a
  web-archive-specific reason not to.
- **Provider seam.** `providers/base.py` defines `Snapshot` (provider,
  timestamp, original/raw/replay URLs, digest, mimetype) and
  `SnapshotProvider` (`list_snapshots`, `fetch`), plus shared HTTP
  (`HttpProvider`, `build_session`, `stream_text`). `core.py` knows nothing
  about any specific archive. New archives register in
  `providers/__init__.PROVIDERS` and appear in `--provider` automatically. A
  capability the backend lacks is either honored client-side where that's
  faithful (Memento does its own `from/to/limit/collapse`) or refused with a
  clear error where faking it would mislead (`--prefix` on a per-URL
  TimeMap) — never silently ignored in a way that returns wrong results.
- **Timestamps stay provider-native strings** (Wayback: `YYYYMMDDhhmmss`),
  which sort lexically; `Snapshot.dt` derives a datetime for display. A
  future provider with a different native format must emit something
  lexically sortable or override ordering.
- **Threads, not asyncio.** Workloads are dozens-to-hundreds of I/O-bound
  fetches; `ThreadPoolExecutor` keeps the code approachable and the
  dependency tree flat. Revisit only if profiling says so.
- **`html.parser`, not lxml.** Zero native dependencies beats speed here;
  extraction is not the bottleneck (the network is).
- **Caching is deliberately deferred** (per the original brief). When it
  lands it should live *inside* providers (keyed by digest, which Wayback
  already supplies) so `core.py` stays cache-oblivious.
- **Testing is offline.** The suite mocks HTTP (`responses`) and uses an
  in-memory fake provider; CI must never hammer archive.org. The env
  overrides `REGREP_WAYBACK_CDX_URL`/`REGREP_WAYBACK_WEB_URL` exist so
  end-to-end runs can target a local stub or a self-hosted pywb.

## Timeline / diff mode (`--timeline`)

The feature that most distinguishes regrep from "grep, but slower over the
network": answering *when* a phrase appeared and disappeared, not just where
it matches.

- **Presence, not just matches.** `run_search` records a `SnapshotPresence`
  (present/absent + a sample line) for every *readable* snapshot, not only
  matching ones — the absent captures are what make a timeline. Unreadable
  snapshots (fetch error, binary, oversize) contribute no record: they tell
  us nothing about whether the phrase was there, so they become gaps rather
  than false "absent" states. This lives in `core` and is populated always;
  it's cheap (a bool + one line reference per snapshot).
- **Segments carry their boundary.** `timeline.build_timeline` collapses the
  chronological presence list into alternating present/absent runs. Each
  segment keeps `prev_end` — the last snapshot of the preceding opposite run
  — so a change is *bracketed* honestly ("appeared between X and Y") rather
  than pinned to a single instant the archive never actually captured.
- **Dedupe must be state-aware.** The client-side digest dedupe (great for
  match output: show each distinct version once) is *wrong* for timelines: a
  phrase that vanishes and returns produces a repeated digest, and dropping
  it would erase the reappearance. So `SearchOptions.dedupe` is `False` in
  timeline mode. Server-side `collapse=digest` stays on in both modes — it
  only removes *adjacent* identical captures, which are the same state
  continuing (no transition lost), and every retained capture becomes a
  candidate transition point. The trade-off: the exact last-seen instant
  within an unchanged run is approximated, which the bracketing already
  makes explicit.
- **Exit code.** `0` if the pattern was ever present, else `1` — the natural
  grep-shaped reading of "did you find it in this URL's history?"
- **Rendering reuses `render_line`** so sample matches get the same
  truncation/coloring as normal output; `--json` emits one
  `timeline-segment` object per run.

## Providers: Wayback (CDX) and the Memento family (archive.today, TimeTravel)

The provider seam earns its keep past the first backend. archive.today and
TimeTravel are both Memento-compliant (RFC 7089), so each is "a Memento
TimeMap provider, pointed at a different endpoint" — the base (`memento.py`)
does the work, and the concrete providers (`archivetoday.py`,
`timetravel.py`) are ~15 lines each. TimeTravel is the proof the abstraction
generalized: adding an aggregator that fans out across *every* archive at
once cost a subclass, one endpoint URL, and two small base refinements
(below), not a rewrite.

- **Shared HTTP, one code path.** Session construction (retry/backoff,
  `Retry-After`, pooling, User-Agent) and the size-capped, binary-aware,
  charset-correct fetch (`stream_text`) moved to `providers/base.py` when the
  second provider arrived, rather than being copied. Wayback and Memento both
  use `HttpProvider._session()` + `stream_text`; Wayback keeps only its
  extra pre-fetch mimetype skip (it has the mimetype from CDX; Memento does
  not).
- **What a TimeMap can't give you** shaped the `Snapshot` contract from the
  start, which is why nothing in `core`/`output`/`timeline` needed changing:
  - *No digest* → `Snapshot.digest is None`; `dedupe_snapshots` is already a
    no-op when digests are absent, so no identical-content collapsing (we
    can't know equality without fetching). Timeline mode is unaffected — it
    keys on fetched presence, not digests.
  - *No raw-bytes endpoint* → `raw_url == replay_url` (the memento URL);
    fetched content includes archive.today's chrome. `--visible` mitigates,
    and the caveat is documented rather than hidden.
  - *No mimetype* → every capture is fetched and NUL-byte-checked; nothing to
    pre-skip.
- **Client-side selection.** A TimeMap returns every capture at once, so
  `from/to/limit/collapse` are applied in the provider after parsing. Partial
  date bounds are padded for lexical comparison (`from="2023"` →
  `20230000000000`, `to="2023"` → `20239999999999`). `--prefix` has no
  TimeMap equivalent, so it *raises* rather than silently returning
  whole-URL results — a wrong answer to an explicit request is worse than an
  error.
- **The comma problem.** Link-format separates entries with commas, but RFC
  1123 datetimes (`"Mon, 01 Apr 2013 ..."`) contain them too. The parser
  splits only on a comma that begins a new link (`,\s*(?=<)`) and pulls
  `datetime`/`rel` from quoted params, so an internal date comma never splits
  an entry. `parse_timemap` is a pure function, tested independently of HTTP.
- **Telling "no captures" from "blocked."** archive.today fronts Cloudflare;
  a challenge page is not a TimeMap. An empty-but-valid TimeMap (has `rel=`)
  returns `[]`; a response with no link-format markers raises a
  ProviderError that names bot-blocking as the likely cause.

### TimeTravel: what an aggregator added to the base

Building the aggregator surfaced two properties the base should have had, so
they live in `MementoTimeMapProvider` and benefit every Memento provider:

- **URL-level dedup.** An aggregated TimeMap routinely lists the *same*
  memento URL from more than one upstream source. An identical URL is the
  same capture, so `_dedupe_urls` keeps the first (after the chronological
  sort, before range/limit) — otherwise duplicates would burn `--limit`
  slots and print twice. (Digest dedup still can't apply; TimeMaps have no
  digests.)
- **Per-provider index timeout.** The base TimeMap request timeout is a class
  attribute (`timemap_timeout`), so TimeTravel — which polls archives live
  and is much slower than a single archive — raises the read half to 120s
  without touching the shared request code.

TimeTravel-specific behavior that is *documented, not worked around*: its
mementos point at each source archive's own replay URL, so Wayback captures
come back as replay pages with toolbar chrome (not `id_` raw bytes) — the
docs steer anyone wanting pristine Wayback content to `--provider wayback`.
Large aggregated TimeMaps are paginated via `rel="timemap"` links; regrep
reads the first page (and `--limit` bounds it further). Following pagination
is future work, not a correctness bug.

## Distribution

PyPI publishing uses **trusted publishing** (OIDC via
`pypa/gh-action-pypi-publish`), so no long-lived API token is stored in the
repo. `release.yml` fires on `v*` tags, verifies the tag equals the version
in both `pyproject.toml` and `__init__.py` (a guard against half-bumped
releases), builds sdist + wheel, and publishes from a `pypi` environment.
The name `regrep` was unclaimed as of 2026-07; first tag push claims it.

## Known limitations (candidates for future work)

- `--visible` line numbers index the extracted text, not the HTML source
  (documented in `--help` and README).
- One URL per invocation; no stdin list of URLs yet.
- No `-v/--invert-match`, `-C/--context`, or per-snapshot match counts yet.
- Providers: Wayback, archive.today, and TimeTravel. The Memento base makes
  further RFC 7089 archives short to add.
- Memento providers (archive.today, TimeTravel): no dedupe by content (no
  digests), fetched pages include each archive's wrapper (no raw endpoint),
  no `--prefix`.
- archive.today may block automated access; TimeTravel is slow, mixes source
  archives, and its large TimeMaps are read first-page-only (no pagination
  following yet).
- No response caching; identical repeat runs re-fetch (digest dedupe only
  helps within a run, and only for providers that supply digests).
- Timeline change points are bracketed, not exact — a run of unchanged
  captures is collapsed server-side, so the true last-seen moment before a
  change is only known to within one snapshot interval.
