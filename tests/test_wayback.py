from urllib.parse import parse_qs, urlparse

import pytest
import responses

from conftest import CDX_URL, cdx_payload, make_snapshot, raw_url
from regrep.providers.base import FetchStatus, ProviderError
from regrep.providers.wayback import WaybackProvider

PAGE = "http://example.com/page"


@responses.activate
def test_list_snapshots_parses_rows():
    responses.get(
        CDX_URL,
        body=cdx_payload(
            [
                ["20200102030405", PAGE, "text/html", "200", "AAA"],
                ["20210102030405", PAGE, "text/html", "200", "BBB"],
            ]
        ),
    )
    snaps = WaybackProvider().list_snapshots(PAGE)
    assert [s.timestamp for s in snaps] == ["20200102030405", "20210102030405"]
    assert snaps[0].original_url == PAGE
    assert snaps[0].raw_url == f"https://web.archive.org/web/20200102030405id_/{PAGE}"
    assert snaps[0].replay_url == f"https://web.archive.org/web/20200102030405/{PAGE}"
    assert snaps[0].digest == "AAA"
    assert snaps[0].mimetype == "text/html"
    assert snaps[0].dt is not None and snaps[0].dt.year == 2020
    assert snaps[0].date_display == "2020-01-02 03:04:05"


@responses.activate
def test_list_snapshots_sends_expected_params():
    responses.get(CDX_URL, body="[]")
    WaybackProvider().list_snapshots(
        PAGE, from_ts="2023", to_ts="202406", limit=25, prefix=True, collapse=8
    )
    query = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert query["url"] == [PAGE]
    assert query["output"] == ["json"]
    assert query["fl"] == ["timestamp,original,mimetype,statuscode,digest"]
    assert query["filter"] == ["statuscode:200"]
    assert query["collapse"] == ["digest", "timestamp:8"]
    assert query["from"] == ["2023"]
    assert query["to"] == ["202406"]
    assert query["limit"] == ["25"]
    assert query["matchType"] == ["prefix"]


@responses.activate
def test_list_snapshots_omits_optional_params_by_default():
    responses.get(CDX_URL, body="[]")
    WaybackProvider().list_snapshots(PAGE)
    query = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert query["collapse"] == ["digest"]
    for absent in ("from", "to", "limit", "matchType"):
        assert absent not in query


@pytest.mark.parametrize("body", ["", "[]", cdx_payload([])])
@responses.activate
def test_list_snapshots_empty_variants(body):
    responses.get(CDX_URL, body=body)
    assert WaybackProvider().list_snapshots(PAGE) == []


@responses.activate
def test_list_snapshots_non_json_raises():
    responses.get(CDX_URL, body="<html>Blocked</html>")
    with pytest.raises(ProviderError, match="unexpected payload"):
        WaybackProvider().list_snapshots(PAGE)


@responses.activate
def test_list_snapshots_http_error_raises():
    responses.get(CDX_URL, status=503)
    with pytest.raises(ProviderError, match="CDX query failed"):
        WaybackProvider().list_snapshots(PAGE)


@responses.activate
def test_list_snapshots_sets_user_agent():
    responses.get(CDX_URL, body="[]")
    WaybackProvider().list_snapshots(PAGE)
    assert responses.calls[0].request.headers["User-Agent"].startswith("regrep/")


@responses.activate
def test_fetch_ok_with_declared_charset():
    snap = make_snapshot("20230101000000", PAGE)
    responses.get(
        raw_url(snap.timestamp, PAGE),
        body="caf\xe9".encode("windows-1252"),
        content_type="text/html; charset=windows-1252",
    )
    result = WaybackProvider().fetch(snap)
    assert result.status is FetchStatus.OK
    assert "café" in result.text


@responses.activate
def test_fetch_detects_encoding_when_charset_missing():
    # requests would otherwise assume ISO-8859-1 for text/* and mangle this
    body = ("Le café des archives — 网络档案馆的咖啡馆。 " * 40).encode("utf-8")
    snap = make_snapshot("20230101000000", PAGE)
    responses.get(raw_url(snap.timestamp, PAGE), body=body, content_type="text/html")
    result = WaybackProvider().fetch(snap)
    assert result.status is FetchStatus.OK
    assert "café" in result.text
    assert "网络档案馆" in result.text


@responses.activate
def test_fetch_skips_known_binary_mimetype_without_request():
    snap = make_snapshot("20230101000000", PAGE, mimetype="image/png")
    result = WaybackProvider().fetch(snap)
    assert result.status is FetchStatus.NON_TEXT
    assert len(responses.calls) == 0


@responses.activate
def test_fetch_nul_bytes_reported_binary():
    snap = make_snapshot("20230101000000", PAGE)
    responses.get(
        raw_url(snap.timestamp, PAGE), body=b"GIF89a\x00\x01\x02", content_type="text/html"
    )
    assert WaybackProvider().fetch(snap).status is FetchStatus.BINARY


@responses.activate
def test_fetch_respects_content_length_cap():
    snap = make_snapshot("20230101000000", PAGE)
    responses.get(
        raw_url(snap.timestamp, PAGE),
        body="x",
        headers={"Content-Length": str(50 * 1024 * 1024)},
    )
    assert WaybackProvider().fetch(snap).status is FetchStatus.TOO_LARGE


@responses.activate
def test_fetch_caps_streamed_size():
    snap = make_snapshot("20230101000000", PAGE)
    responses.get(raw_url(snap.timestamp, PAGE), body=b"y" * 4096, content_type="text/html")
    result = WaybackProvider().fetch(snap, max_bytes=1024)
    assert result.status is FetchStatus.TOO_LARGE


@responses.activate
def test_fetch_http_error_status():
    snap = make_snapshot("20230101000000", PAGE)
    responses.get(raw_url(snap.timestamp, PAGE), status=404)
    result = WaybackProvider().fetch(snap)
    assert result.status is FetchStatus.ERROR
    assert "404" in result.detail


def test_env_overrides_point_elsewhere(monkeypatch):
    monkeypatch.setenv("REGREP_WAYBACK_CDX_URL", "http://localhost:9/cdx")
    monkeypatch.setenv("REGREP_WAYBACK_WEB_URL", "http://localhost:9/web/")
    provider = WaybackProvider()
    assert provider.cdx_url == "http://localhost:9/cdx"
    assert provider.web_url == "http://localhost:9/web"  # trailing slash trimmed


def test_session_has_polite_retry_policy():
    session = WaybackProvider()._session()
    retries = session.get_adapter("https://web.archive.org").max_retries
    assert retries.total == 3
    assert 429 in retries.status_forcelist
    assert session.headers["User-Agent"].startswith("regrep/")
