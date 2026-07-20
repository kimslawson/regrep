import json

import pytest
import responses

from conftest import CDX_URL, cdx_payload, raw_url
from regrep.cli import main, timestamp_arg

PAGE = "http://example.com/page"


def mock_history(pages: dict[str, str | bytes]):
    """Register a CDX listing plus one raw-content response per timestamp."""
    rows = [[ts, PAGE, "text/html", "200", f"D{ts}"] for ts in pages]
    responses.get(CDX_URL, body=cdx_payload(rows))
    for ts, body in pages.items():
        responses.get(raw_url(ts, PAGE), body=body, content_type="text/html")


@responses.activate
def test_match_exits_zero_and_prints_chronologically(capsys):
    mock_history(
        {
            "20240101000000": "<p>newer needle</p>",
            "20200101000000": "<p>older needle</p>",
        }
    )
    code = main(["needle", PAGE, "--color", "never", "-q"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.index("older needle") < captured.out.index("newer needle")
    assert "2020-01-01 00:00:00" in captured.out
    assert "\033[" not in captured.out


@responses.activate
def test_no_match_exits_one(capsys):
    mock_history({"20200101000000": "<p>nothing to see</p>"})
    assert main(["needle", PAGE, "--color", "never", "-q"]) == 1
    assert capsys.readouterr().out == ""


@responses.activate
def test_no_snapshots_exits_one(capsys):
    responses.get(CDX_URL, body="[]")
    assert main(["needle", PAGE, "-q"]) == 1


@responses.activate
def test_cdx_failure_exits_two(capsys):
    responses.get(CDX_URL, status=500)
    assert main(["needle", PAGE, "-q"]) == 2
    assert "regrep:" in capsys.readouterr().err


def test_invalid_regex_exits_two(capsys):
    assert main(["(unclosed", PAGE]) == 2
    assert "invalid regex" in capsys.readouterr().err


def test_invalid_date_exits_two(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["needle", PAGE, "--from", "not-a-date"])
    assert excinfo.value.code == 2


def test_conflicting_cadence_flags_exit_two():
    with pytest.raises(SystemExit) as excinfo:
        main(["needle", PAGE, "--daily", "--monthly"])
    assert excinfo.value.code == 2


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("regrep ")


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("2023", "2023"),
        ("2023-06", "202306"),
        ("2023-06-15", "20230615"),
        ("20230615123059", "20230615123059"),
    ],
)
def test_timestamp_normalization(raw, normalized):
    assert timestamp_arg(raw) == normalized


@responses.activate
def test_case_sensitive_by_default(capsys):
    mock_history({"20200101000000": "<p>Needle</p>"})
    assert main(["needle", PAGE, "--color", "never", "-q"]) == 1


@responses.activate
def test_ignore_case_flag(capsys):
    mock_history({"20200101000000": "<p>Needle</p>"})
    assert main(["-i", "needle", PAGE, "--color", "never", "-q"]) == 0


@responses.activate
def test_fixed_strings_flag(capsys):
    mock_history({"20200101000000": "<p>a.c</p>\n<p>abc</p>"})
    assert main(["-F", "a.c", PAGE, "--color", "never", "-q"]) == 0
    out = capsys.readouterr().out
    assert "a.c" in out
    assert "abc" not in out


@responses.activate
def test_visible_flag_ignores_script_content(capsys):
    html = "<script>var needle;</script><p>plain text</p>"
    mock_history({"20200101000000": html})
    assert main(["needle", PAGE, "--visible", "--color", "never", "-q"]) == 1
    mock_history({"20210101000000": html})
    assert main(["plain", PAGE, "--visible", "--color", "never", "-q"]) == 0


@responses.activate
def test_json_output_is_parseable(capsys):
    mock_history({"20200101000000": "<p>one needle</p>"})
    assert main(["needle", PAGE, "--json", "-q"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines
    for line in lines:
        record = json.loads(line)
        assert record["type"] == "match"
        assert record["provider"] == "wayback"
        start, end = record["spans"][0]
        assert record["line"][start:end] == "needle"


@responses.activate
def test_summary_goes_to_stderr_not_stdout(capsys):
    mock_history({"20200101000000": "<p>needle</p>"})
    assert main(["needle", PAGE, "--color", "never"]) == 0
    captured = capsys.readouterr()
    assert "snapshot(s) scanned" in captured.err
    assert "snapshot(s) scanned" not in captured.out


@responses.activate
def test_limit_flag_reaches_cdx_query(capsys):
    responses.get(CDX_URL, body="[]")
    main(["needle", PAGE, "-l", "7", "-q"])
    assert "limit=7" in responses.calls[0].request.url


@responses.activate
def test_timeline_reports_appear_and_vanish(capsys):
    mock_history(
        {
            "20180101000000": "<p>nothing yet</p>",
            "20190101000000": "<p>the needle arrives</p>",
            "20200101000000": "<p>needle still here</p>",
            "20210101000000": "<p>gone again</p>",
        }
    )
    assert main(["needle", PAGE, "--timeline", "--color", "never", "-q"]) == 0
    out = capsys.readouterr().out
    assert "+ present" in out
    assert "- absent" in out
    assert "appeared between" in out
    assert "vanished between" in out


@responses.activate
def test_timeline_exit_one_when_never_present(capsys):
    mock_history({"20200101000000": "<p>no match here</p>"})
    assert main(["needle", PAGE, "--timeline", "-q"]) == 1


@responses.activate
def test_timeline_json_emits_segment_records(capsys):
    mock_history(
        {
            "20180101000000": "<p>nope</p>",
            "20190101000000": "<p>needle</p>",
        }
    )
    assert main(["needle", PAGE, "--timeline", "--json", "-q"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["type"] for r in records] == ["timeline-segment", "timeline-segment"]
    assert records[-1]["present"] is True


def test_unknown_provider_lists_choices(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["needle", PAGE, "--provider", "bogus"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "archivetoday" in err and "wayback" in err and "timetravel" in err


@responses.activate
def test_provider_timetravel_end_to_end(capsys, monkeypatch):
    monkeypatch.setenv("REGREP_TIMETRAVEL_TIMEMAP_URL", "https://tt.test/timemap/link/")
    endpoint = "https://tt.test/timemap/link/" + PAGE
    timemap = (
        f'<{PAGE}>; rel="original",\n'
        f'<https://web.archive.org/web/20130101000000/{PAGE}>; rel="first memento"; '
        'datetime="Tue, 01 Jan 2013 00:00:00 GMT",\n'
        f'<https://archive.ph/zzzz/{PAGE}>; rel="last memento"; '
        'datetime="Wed, 20 Jul 2022 10:00:00 GMT"'
    )
    responses.get(endpoint, body=timemap)
    responses.get(
        f"https://web.archive.org/web/20130101000000/{PAGE}", body="<p>wayback needle</p>"
    )
    responses.get(f"https://archive.ph/zzzz/{PAGE}", body="<p>today needle</p>")
    assert main(["needle", PAGE, "--provider", "timetravel", "--color", "never", "-q"]) == 0
    out = capsys.readouterr().out
    assert "wayback needle" in out and "today needle" in out
    # Results span both source archives.
    assert "web.archive.org/web/20130101000000" in out
    assert "archive.ph/zzzz" in out


@responses.activate
def test_provider_archivetoday_end_to_end(capsys, monkeypatch):
    monkeypatch.setenv("REGREP_ARCHIVETODAY_TIMEMAP_URL", "https://at.test/timemap/")
    endpoint = "https://at.test/timemap/" + PAGE
    timemap = (
        f'<{PAGE}>; rel="original",\n'
        f'<https://archive.ph/1111/{PAGE}>; rel="first memento"; '
        'datetime="Mon, 01 Apr 2013 00:00:00 GMT",\n'
        f'<https://archive.ph/2222/{PAGE}>; rel="last memento"; '
        'datetime="Wed, 20 Jul 2022 10:00:00 GMT"'
    )
    responses.get(endpoint, body=timemap)
    responses.get(f"https://archive.ph/1111/{PAGE}", body="<p>old needle</p>")
    responses.get(f"https://archive.ph/2222/{PAGE}", body="<p>new needle</p>")
    assert main(["needle", PAGE, "--provider", "archivetoday", "--color", "never", "-q"]) == 0
    out = capsys.readouterr().out
    assert "old needle" in out and "new needle" in out
    assert "archive.ph/1111" in out  # memento URL shown as the source link


@responses.activate
def test_provider_archivetoday_timeline(capsys, monkeypatch):
    monkeypatch.setenv("REGREP_ARCHIVETODAY_TIMEMAP_URL", "https://at.test/timemap/")
    endpoint = "https://at.test/timemap/" + PAGE
    timemap = (
        f'<{PAGE}>; rel="original",\n'
        f'<https://archive.ph/1111/{PAGE}>; rel="first memento"; '
        'datetime="Mon, 01 Apr 2018 00:00:00 GMT",\n'
        f'<https://archive.ph/2222/{PAGE}>; rel="memento"; '
        'datetime="Tue, 01 Apr 2019 00:00:00 GMT",\n'
        f'<https://archive.ph/3333/{PAGE}>; rel="last memento"; '
        'datetime="Thu, 01 Apr 2021 00:00:00 GMT"'
    )
    responses.get(endpoint, body=timemap)
    responses.get(f"https://archive.ph/1111/{PAGE}", body="<p>nothing yet</p>")
    responses.get(f"https://archive.ph/2222/{PAGE}", body="<p>needle appears</p>")
    responses.get(f"https://archive.ph/3333/{PAGE}", body="<p>it was removed</p>")
    assert main(["needle", PAGE, "--provider", "archivetoday", "--timeline", "-q"]) == 0
    out = capsys.readouterr().out
    assert "appeared between" in out and "vanished between" in out


@responses.activate
def test_timeline_keeps_duplicate_digests_for_reappearance(capsys):
    # Same content (digest DUP) at t1 and t3, different content at t2. Normal
    # dedupe would drop the t3 duplicate; --timeline must keep it so the
    # phrase's reappearance is detected.
    rows = [
        ["20200101000000", PAGE, "text/html", "200", "DUP"],
        ["20210101000000", PAGE, "text/html", "200", "GONE"],
        ["20220101000000", PAGE, "text/html", "200", "DUP"],
    ]
    responses.get(CDX_URL, body=cdx_payload(rows))
    responses.get(raw_url("20200101000000", PAGE), body="<p>needle</p>", content_type="text/html")
    responses.get(raw_url("20210101000000", PAGE), body="<p>vanished</p>", content_type="text/html")
    responses.get(raw_url("20220101000000", PAGE), body="<p>needle</p>", content_type="text/html")
    assert main(["needle", PAGE, "--timeline", "--json", "-q"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["present"] for r in records] == [True, False, True]
