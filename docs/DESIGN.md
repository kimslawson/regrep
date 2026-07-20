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
  `SnapshotProvider` (`list_snapshots`, `fetch`). `core.py` knows nothing
  about Wayback. New archives register in `providers/__init__.PROVIDERS` and
  appear in `--provider` automatically. Capability hints a backend can't
  honor (e.g. `collapse` on non-CDX archives) may be ignored rather than
  faked.
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

## Known limitations (candidates for future work)

- `--visible` line numbers index the extracted text, not the HTML source
  (documented in `--help` and README).
- One URL per invocation; no stdin list of URLs yet.
- No `-v/--invert-match`, `-C/--context`, or per-snapshot match counts yet.
- Wayback is the only provider; TimeTravel/archive.today are roadmap.
- No response caching; identical repeat runs re-fetch (digest dedupe only
  helps within a run).
