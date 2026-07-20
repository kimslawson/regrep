import pytest
import responses

from regrep.providers.base import FetchStatus, ProviderError, Snapshot
from regrep.providers.memento import MementoTimeMapProvider, parse_timemap

TM_BASE = "https://tm.test/timemap/"
URL = "http://example.com/page"
ENDPOINT = "https://tm.test/timemap/http://example.com/page"


def link_format(original: str, entries: list[tuple[str, str]], meta: bool = True) -> str:
    """Build an RFC 7089 link-format TimeMap. `entries` are (rfc1123, url)."""
    links = []
    if meta:
        links.append(f'<{original}>; rel="original"')
        links.append(
            f'<https://tm.test/timemap/{original}>; rel="self"; type="application/link-format"'
        )
        links.append(f'<https://tm.test/timegate/{original}>; rel="timegate"')
    n = len(entries)
    for i, (dt, url) in enumerate(entries):
        if n == 1:
            rel = "first last memento"
        elif i == 0:
            rel = "first memento"
        elif i == n - 1:
            rel = "last memento"
        else:
            rel = "memento"
        links.append(f'<{url}>; rel="{rel}"; datetime="{dt}"')
    return ",\n".join(links)


def provider() -> MementoTimeMapProvider:
    return MementoTimeMapProvider(timemap_url=TM_BASE)


# --- parse_timemap ---------------------------------------------------------


def test_parse_handles_commas_inside_datetimes():
    # The RFC 1123 datetime "Mon, 01 Apr ..." contains a comma; the parser
    # must not treat it as a link separator.
    text = link_format(
        URL,
        [
            ("Mon, 01 Apr 2013 00:00:00 GMT", "https://tm.test/20130401000000/" + URL),
            ("Wed, 20 Jul 2022 10:30:00 GMT", "https://tm.test/20220720103000/" + URL),
        ],
    )
    mementos, original = parse_timemap(text)
    assert original == URL
    assert mementos == [
        ("20130401000000", "https://tm.test/20130401000000/" + URL),
        ("20220720103000", "https://tm.test/20220720103000/" + URL),
    ]


def test_parse_excludes_original_self_timegate():
    text = link_format(URL, [("Mon, 01 Apr 2013 00:00:00 GMT", "https://tm.test/a")])
    mementos, _ = parse_timemap(text)
    assert mementos == [("20130401000000", "https://tm.test/a")]


def test_parse_counts_first_and_last_memento_rels():
    text = link_format(
        URL,
        [
            ("Mon, 01 Apr 2013 00:00:00 GMT", "https://tm.test/a"),
            ("Tue, 02 Apr 2013 00:00:00 GMT", "https://tm.test/b"),
            ("Wed, 03 Apr 2013 00:00:00 GMT", "https://tm.test/c"),
        ],
    )
    mementos, _ = parse_timemap(text)
    assert [ts for ts, _ in mementos] == ["20130401000000", "20130402000000", "20130403000000"]


def test_parse_converts_offset_to_utc():
    text = link_format(URL, [("Mon, 01 Apr 2013 02:00:00 +0200", "https://tm.test/a")])
    mementos, _ = parse_timemap(text)
    assert mementos[0][0] == "20130401000000"


def test_parse_empty_and_garbage():
    assert parse_timemap("") == ([], None)
    assert parse_timemap("total nonsense, no links here") == ([], None)


# --- list_snapshots --------------------------------------------------------


@responses.activate
def test_list_snapshots_builds_neutral_snapshots():
    responses.get(
        ENDPOINT,
        body=link_format(
            URL,
            [
                ("Mon, 01 Apr 2013 00:00:00 GMT", "https://tm.test/20130401000000/" + URL),
                ("Wed, 20 Jul 2022 10:30:00 GMT", "https://tm.test/20220720103000/" + URL),
            ],
        ),
    )
    snaps = provider().list_snapshots(URL)
    assert [s.timestamp for s in snaps] == ["20130401000000", "20220720103000"]
    assert all(s.provider == "memento" for s in snaps)
    # No digest or mimetype from a TimeMap; raw == replay (no id_ endpoint).
    assert all(s.digest is None and s.mimetype is None for s in snaps)
    assert snaps[0].raw_url == snaps[0].replay_url == "https://tm.test/20130401000000/" + URL
    assert snaps[0].original_url == URL
    assert snaps[0].date_display == "2013-04-01 00:00:00"


@responses.activate
def test_from_to_filter_is_client_side():
    responses.get(
        ENDPOINT,
        body=link_format(
            URL,
            [
                ("Mon, 01 Apr 2013 00:00:00 GMT", "https://tm.test/a"),
                ("Tue, 01 Apr 2014 00:00:00 GMT", "https://tm.test/b"),
                ("Wed, 01 Apr 2015 00:00:00 GMT", "https://tm.test/c"),
            ],
        ),
    )
    snaps = provider().list_snapshots(URL, from_ts="2014", to_ts="2014")
    assert [s.timestamp for s in snaps] == ["20140401000000"]


@responses.activate
def test_collapse_keeps_earliest_per_period():
    responses.get(
        ENDPOINT,
        body=link_format(
            URL,
            [
                ("Mon, 01 Jan 2013 00:00:00 GMT", "https://tm.test/a"),
                ("Tue, 15 Jan 2013 00:00:00 GMT", "https://tm.test/b"),
                ("Fri, 01 Feb 2013 00:00:00 GMT", "https://tm.test/c"),
            ],
        ),
    )
    snaps = provider().list_snapshots(URL, collapse=6)  # monthly
    assert [s.timestamp for s in snaps] == ["20130101000000", "20130201000000"]


@responses.activate
def test_limit_takes_earliest_n():
    entries = [
        (f"Mon, 01 Jan 20{10 + i:02d} 00:00:00 GMT", f"https://tm.test/{i}") for i in range(5)
    ]
    responses.get(ENDPOINT, body=link_format(URL, entries))
    snaps = provider().list_snapshots(URL, limit=2)
    assert [s.timestamp for s in snaps] == ["20100101000000", "20110101000000"]


def test_prefix_is_refused_without_a_request():
    with pytest.raises(ProviderError, match="prefix"):
        provider().list_snapshots(URL, prefix=True)


@responses.activate
def test_empty_but_valid_timemap_returns_empty():
    body = (
        f'<{URL}>; rel="original",\n'
        f'<https://tm.test/timemap/{URL}>; rel="self"; type="application/link-format"'
    )
    responses.get(ENDPOINT, body=body)
    assert provider().list_snapshots(URL) == []


@responses.activate
def test_bot_block_page_raises_helpfully():
    responses.get(ENDPOINT, body="<html>Attention Required! | Cloudflare</html>")
    with pytest.raises(ProviderError, match="blocking automated access"):
        provider().list_snapshots(URL)


@responses.activate
def test_timemap_http_error_raises():
    responses.get(ENDPOINT, status=503)
    with pytest.raises(ProviderError, match="TimeMap query failed"):
        provider().list_snapshots(URL)


@responses.activate
def test_endpoint_url_and_user_agent():
    responses.get(ENDPOINT, body=link_format(URL, [("Mon, 01 Apr 2013 00:00:00 GMT", "u")]))
    provider().list_snapshots(URL)
    request = responses.calls[0].request
    assert request.url.startswith("https://tm.test/timemap/")
    assert "example.com/page" in request.url
    assert request.headers["User-Agent"].startswith("regrep/")


def test_missing_endpoint_config_raises():
    with pytest.raises(ProviderError, match="no TimeMap endpoint"):
        MementoTimeMapProvider()


# --- fetch -----------------------------------------------------------------


@responses.activate
def test_fetch_ok():
    url = "https://tm.test/20130401000000/" + URL
    responses.get(url, body="<p>archived needle</p>", content_type="text/html")
    snap = Snapshot("memento", "20130401000000", URL, url, url)
    result = provider().fetch(snap)
    assert result.status is FetchStatus.OK
    assert "needle" in result.text


@responses.activate
def test_fetch_binary_detected():
    url = "https://tm.test/img"
    responses.get(url, body=b"\x89PNG\x00\x00binary", content_type="text/html")
    snap = Snapshot("memento", "20130401000000", URL, url, url)
    assert provider().fetch(snap).status is FetchStatus.BINARY
