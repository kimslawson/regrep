import responses

from regrep.providers import PROVIDERS, get_provider
from regrep.providers.memento import MementoTimeMapProvider
from regrep.providers.timetravel import TimeTravelProvider
from test_memento import URL, link_format


def wb(ts: str) -> str:
    return f"https://web.archive.org/web/{ts}/{URL}"


def ph(code: str) -> str:
    return f"https://archive.ph/{code}/{URL}"


def test_timetravel_is_registered():
    assert "timetravel" in PROVIDERS
    assert get_provider("timetravel").name == "timetravel"


def test_default_endpoint_selects_link_format():
    url = TimeTravelProvider().timemap_url
    assert url.startswith("https://timetravel.mementoweb.org/timemap/link")


def test_env_var_overrides_endpoint(monkeypatch):
    monkeypatch.setenv("REGREP_TIMETRAVEL_TIMEMAP_URL", "http://localhost:9/timemap/link/")
    assert TimeTravelProvider().timemap_url == "http://localhost:9/timemap/link/"


def test_aggregator_gets_a_longer_index_timeout():
    # Aggregator polls archives live, so its index read timeout is raised
    # above the base Memento default.
    assert TimeTravelProvider.timemap_timeout[1] > MementoTimeMapProvider.timemap_timeout[1]


@responses.activate
def test_end_to_end_spans_multiple_archives(monkeypatch):
    monkeypatch.setenv("REGREP_TIMETRAVEL_TIMEMAP_URL", "https://tt.test/timemap/link/")
    endpoint = "https://tt.test/timemap/link/" + URL
    # An aggregated TimeMap: mementos live on different archive hosts.
    responses.get(
        endpoint,
        body=link_format(
            URL,
            [
                ("Wed, 01 Jan 1997 00:00:00 GMT", wb("19970101000000")),
                ("Mon, 01 Apr 2013 00:00:00 GMT", ph("aaaa")),
                ("Wed, 20 Jul 2022 10:00:00 GMT", wb("20220720100000")),
            ],
        ),
    )
    snaps = TimeTravelProvider().list_snapshots(URL)
    assert [s.timestamp for s in snaps] == [
        "19970101000000",
        "20130401000000",
        "20220720100000",
    ]
    # Mementos keep their source-archive URLs; provider tag is timetravel.
    assert snaps[0].raw_url.startswith("https://web.archive.org/web/")
    assert snaps[1].raw_url.startswith("https://archive.ph/")
    assert all(s.provider == "timetravel" for s in snaps)


@responses.activate
def test_from_to_filter_applies_to_aggregated_results(monkeypatch):
    monkeypatch.setenv("REGREP_TIMETRAVEL_TIMEMAP_URL", "https://tt.test/timemap/link/")
    endpoint = "https://tt.test/timemap/link/" + URL
    responses.get(
        endpoint,
        body=link_format(
            URL,
            [
                ("Wed, 01 Jan 1997 00:00:00 GMT", wb("19970101000000")),
                ("Mon, 01 Apr 2013 00:00:00 GMT", ph("aaaa")),
                ("Wed, 20 Jul 2022 10:00:00 GMT", wb("20220720100000")),
            ],
        ),
    )
    snaps = TimeTravelProvider().list_snapshots(URL, from_ts="2010", to_ts="2015")
    assert [s.timestamp for s in snaps] == ["20130401000000"]
