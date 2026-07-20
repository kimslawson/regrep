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
