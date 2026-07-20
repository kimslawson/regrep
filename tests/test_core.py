import re

from conftest import make_snapshot
from regrep.core import SearchOptions, dedupe_snapshots, match_lines, run_search
from regrep.providers.base import FetchResult, FetchStatus, SnapshotProvider


class FakeProvider(SnapshotProvider):
    """In-memory provider: no HTTP, fully deterministic."""

    name = "fake"

    def __init__(self, snapshots, fetches):
        self.snapshots = snapshots
        self.fetches = fetches  # timestamp -> FetchResult

    def list_snapshots(self, url, **kwargs):
        return self.snapshots

    def fetch(self, snapshot, **kwargs):
        return self.fetches[snapshot.timestamp]


def ok(text: str) -> FetchResult:
    return FetchResult(FetchStatus.OK, text=text)


def test_match_lines_numbers_and_spans():
    text = "alpha\nbeta beta\ngamma"
    matches = match_lines(text, re.compile(r"beta"))
    assert len(matches) == 1
    assert matches[0].number == 2
    assert matches[0].spans == ((0, 4), (5, 9))


def test_match_lines_zero_width_pattern_still_matches():
    matches = match_lines("abc", re.compile(r"x*"))
    assert len(matches) == 1  # grep semantics: x* matches every line


def test_dedupe_drops_nonadjacent_duplicate_digests():
    snaps = [
        make_snapshot("20200101000000", digest="X"),
        make_snapshot("20210101000000", digest="Y"),
        make_snapshot("20220101000000", digest="X"),
        make_snapshot("20230101000000", digest=None),  # no digest: never dropped
    ]
    unique, skipped = dedupe_snapshots(snaps)
    assert [s.timestamp for s in unique] == [
        "20200101000000",
        "20210101000000",
        "20230101000000",
    ]
    assert skipped == 1


def test_results_come_back_chronological():
    # Listed newest-first on purpose; concurrent completion order is random.
    stamps = [f"202{i}0101000000" for i in (5, 3, 1, 4, 2, 0)]
    provider = FakeProvider(
        [make_snapshot(ts, digest=ts) for ts in stamps],
        {ts: ok("the needle is here") for ts in stamps},
    )
    outcome = run_search(provider, "u", re.compile("needle"), SearchOptions(workers=4))
    assert [r.snapshot.timestamp for r in outcome.results] == sorted(stamps)
    assert outcome.stats.matched == 6


def test_stats_bucket_every_outcome():
    snaps = [make_snapshot(f"2020010100000{i}", digest=str(i)) for i in range(6)]
    provider = FakeProvider(
        snaps,
        {
            "20200101000000": ok("has needle"),
            "20200101000001": ok("nothing here"),
            "20200101000002": FetchResult(FetchStatus.ERROR, detail="HTTP 404"),
            "20200101000003": FetchResult(FetchStatus.BINARY),
            "20200101000004": FetchResult(FetchStatus.NON_TEXT, detail="image/png"),
            "20200101000005": FetchResult(FetchStatus.TOO_LARGE, detail="99 bytes"),
        },
    )
    outcome = run_search(provider, "u", re.compile("needle"), SearchOptions(workers=3))
    stats = outcome.stats
    assert stats.listed == 6
    assert stats.scanned == 6
    assert stats.matched == 1
    assert stats.errors == 1
    assert stats.binary == 1
    assert stats.non_text == 1
    assert stats.too_large == 1
    assert stats.error_samples == ["20200101000002: HTTP 404"]
    assert len(outcome.results) == 1


def test_visible_mode_extracts_before_matching():
    html = "<html><body><script>var needle=1;</script><p>clean text</p></body></html>"
    provider = FakeProvider(
        [make_snapshot("20200101000000")], {"20200101000000": ok(html)}
    )
    hidden = run_search(provider, "u", re.compile("needle"), SearchOptions(visible=True))
    assert hidden.results == []
    shown = run_search(provider, "u", re.compile("clean"), SearchOptions(visible=True))
    assert len(shown.results) == 1


def test_progress_callback_sees_every_snapshot():
    snaps = [make_snapshot(f"2020010100000{i}", digest=str(i)) for i in range(4)]
    provider = FakeProvider(snaps, {s.timestamp: ok("x") for s in snaps})
    seen = []
    run_search(
        provider,
        "u",
        re.compile("nomatch"),
        SearchOptions(workers=2),
        progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_empty_listing_short_circuits():
    provider = FakeProvider([], {})
    outcome = run_search(provider, "u", re.compile("x"), SearchOptions())
    assert outcome.results == []
    assert outcome.stats.listed == 0
    assert outcome.stats.scanned == 0
