"""Provider-agnostic search engine: dedupe, fetch concurrently, match, order."""

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .extract import extract_visible
from .providers.base import FetchStatus, Snapshot, SnapshotProvider

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class SearchOptions:
    visible: bool = False
    workers: int = 5
    timeout: float = 30.0
    max_bytes: int = 10 * 1024 * 1024
    from_ts: str | None = None
    to_ts: str | None = None
    limit: int | None = 100
    prefix: bool = False
    collapse: int | None = None
    # Drop non-adjacent captures with an identical content digest. Right for
    # match output (each distinct version shown once), but timeline mode turns
    # this off: a phrase vanishing and later returning produces a repeated
    # digest that must be kept for the reappearance to be detected.
    dedupe: bool = True


@dataclass(frozen=True)
class LineMatch:
    number: int
    text: str
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class SnapshotResult:
    snapshot: Snapshot
    lines: tuple[LineMatch, ...]


@dataclass(frozen=True)
class SnapshotPresence:
    """Whether the pattern was present in one successfully-fetched snapshot.

    Recorded for every readable snapshot (matching or not) so timeline mode
    can see the absent states between matches. Unreadable snapshots (fetch
    error, binary, too large) contribute no presence record — they tell us
    nothing about whether the phrase was there.
    """

    snapshot: Snapshot
    present: bool
    sample: LineMatch | None = None  # first matching line, when present


@dataclass
class SearchStats:
    listed: int = 0  # snapshots the provider reported
    duplicates: int = 0  # skipped: identical digest already scanned
    scanned: int = 0  # actually fetched and searched
    matched: int = 0
    errors: int = 0
    binary: int = 0
    non_text: int = 0
    too_large: int = 0
    error_samples: list[str] = field(default_factory=list)


@dataclass
class SearchOutcome:
    results: list[SnapshotResult]
    stats: SearchStats
    # Every readable snapshot in chronological order, matching or not. Feeds
    # timeline mode; empty-by-default keeps the common (results, stats)
    # construction working.
    presence: list[SnapshotPresence] = field(default_factory=list)


def match_lines(text: str, regex: re.Pattern) -> list[LineMatch]:
    matches = []
    for number, line in enumerate(text.splitlines(), 1):
        if regex.search(line):
            spans = tuple((m.start(), m.end()) for m in regex.finditer(line))
            matches.append(LineMatch(number, line, spans))
    return matches


def _scan_one(
    provider: SnapshotProvider,
    snapshot: Snapshot,
    regex: re.Pattern,
    options: SearchOptions,
) -> tuple[Snapshot, FetchStatus, object]:
    result = provider.fetch(snapshot, timeout=options.timeout, max_bytes=options.max_bytes)
    if result.status is not FetchStatus.OK:
        return snapshot, result.status, result.detail
    text = extract_visible(result.text) if options.visible else result.text
    return snapshot, FetchStatus.OK, match_lines(text, regex)


def dedupe_snapshots(snapshots: list[Snapshot]) -> tuple[list[Snapshot], int]:
    """Drop snapshots whose content digest was already seen (any distance apart)."""
    seen: set[str] = set()
    unique = []
    skipped = 0
    for snapshot in snapshots:
        if snapshot.digest:
            if snapshot.digest in seen:
                skipped += 1
                continue
            seen.add(snapshot.digest)
        unique.append(snapshot)
    return unique, skipped


def run_search(
    provider: SnapshotProvider,
    url: str,
    regex: re.Pattern,
    options: SearchOptions,
    progress: ProgressCallback | None = None,
) -> SearchOutcome:
    """List, dedupe, and concurrently scan snapshots. May raise ProviderError.

    Results are returned in chronological order regardless of fetch
    completion order.
    """
    stats = SearchStats()
    snapshots = provider.list_snapshots(
        url,
        from_ts=options.from_ts,
        to_ts=options.to_ts,
        limit=options.limit,
        prefix=options.prefix,
        collapse=options.collapse,
    )
    stats.listed = len(snapshots)
    if options.dedupe:
        snapshots, stats.duplicates = dedupe_snapshots(snapshots)
    if not snapshots:
        return SearchOutcome([], stats)

    results: list[SnapshotResult] = []
    presence: list[SnapshotPresence] = []
    workers = max(1, min(options.workers, len(snapshots)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_scan_one, provider, snapshot, regex, options)
            for snapshot in snapshots
        ]
        try:
            for done, future in enumerate(as_completed(futures), 1):
                snapshot, status, payload = future.result()
                stats.scanned += 1
                if status is FetchStatus.OK:
                    lines = tuple(payload)
                    presence.append(
                        SnapshotPresence(snapshot, bool(lines), lines[0] if lines else None)
                    )
                    if lines:
                        stats.matched += 1
                        results.append(SnapshotResult(snapshot, lines))
                elif status is FetchStatus.ERROR:
                    stats.errors += 1
                    if len(stats.error_samples) < 5:
                        stats.error_samples.append(f"{snapshot.timestamp}: {payload}")
                elif status is FetchStatus.BINARY:
                    stats.binary += 1
                elif status is FetchStatus.NON_TEXT:
                    stats.non_text += 1
                elif status is FetchStatus.TOO_LARGE:
                    stats.too_large += 1
                if progress:
                    progress(done, len(snapshots))
        except KeyboardInterrupt:
            executor.shutdown(wait=False, cancel_futures=True)
            raise

    results.sort(key=lambda r: _chrono_key(r.snapshot))
    presence.sort(key=lambda p: _chrono_key(p.snapshot))
    return SearchOutcome(results, stats, presence)


def _chrono_key(snapshot: Snapshot) -> tuple[str, str]:
    return (snapshot.timestamp, snapshot.original_url)
