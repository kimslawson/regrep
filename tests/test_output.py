import io
import json

from conftest import make_snapshot
from regrep.core import LineMatch, SearchOutcome, SearchStats, SnapshotResult
from regrep.output import (
    ANSI,
    PLAIN,
    colorize_spans,
    make_palette,
    print_json,
    print_results,
    render_line,
    summary_line,
)

RED_X = f"{ANSI.red}x{ANSI.reset}"


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_colorize_multiple_spans():
    out = colorize_spans("x then x", [(0, 1), (7, 8)], ANSI)
    assert out == f"{RED_X} then {RED_X}"


def test_colorize_zero_width_spans_add_nothing():
    assert colorize_spans("abc", [(0, 0), (1, 1)], ANSI) == "abc"


def test_colorize_plain_palette_is_identity():
    assert colorize_spans("x then x", [(0, 1)], PLAIN) == "x then x"


def test_render_line_short_lines_untouched():
    assert render_line("hit x here", [(4, 5)], PLAIN, max_columns=200) == "hit x here"


def test_render_line_zero_max_columns_never_truncates():
    line = "a" * 5000 + "x"
    rendered = render_line(line, [(5000, 5001)], PLAIN, max_columns=0)
    assert rendered == line


def test_render_line_windows_around_match():
    line = "a" * 600 + "needle" + "b" * 600
    rendered = render_line(line, [(600, 606)], ANSI, max_columns=100)
    assert "needle" in rendered
    assert rendered.startswith("…")
    assert rendered.endswith("…")
    assert f"{ANSI.red}needle{ANSI.reset}" in rendered
    # window plus two ellipses and one color pair, nothing more
    visible = rendered.replace(ANSI.red, "").replace(ANSI.reset, "")
    assert len(visible) == 100 + 2


def test_render_line_match_at_start_has_no_leading_ellipsis():
    line = "needle" + "b" * 600
    rendered = render_line(line, [(0, 6)], PLAIN, max_columns=100)
    assert rendered.startswith("needle")
    assert rendered.endswith("…")


def test_make_palette_modes(monkeypatch):
    assert make_palette("always", io.StringIO()) is ANSI
    assert make_palette("never", FakeTTY()) is PLAIN
    assert make_palette("auto", io.StringIO()) is PLAIN  # not a tty
    assert make_palette("auto", FakeTTY()) is ANSI
    monkeypatch.setenv("NO_COLOR", "1")
    assert make_palette("auto", FakeTTY()) is PLAIN
    assert make_palette("always", FakeTTY()) is ANSI  # explicit always wins
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert make_palette("auto", FakeTTY()) is PLAIN


def outcome_with_one_match() -> SearchOutcome:
    snapshot = make_snapshot("20231108120005", "http://example.com/page")
    line = LineMatch(number=42, text="before needle after", spans=((7, 13),))
    stats = SearchStats(listed=1, scanned=1, matched=1)
    return SearchOutcome([SnapshotResult(snapshot, (line,))], stats)


def test_print_results_layout_plain():
    buffer = io.StringIO()
    print_results(outcome_with_one_match(), PLAIN, out=buffer)
    lines = buffer.getvalue().splitlines()
    assert lines[0] == "2023-11-08 12:00:05  http://example.com/page"
    assert lines[1] == "https://web.archive.org/web/20231108120005/http://example.com/page"
    assert lines[2] == "42:before needle after"
    assert "\033[" not in buffer.getvalue()


def test_print_results_layout_ansi():
    buffer = io.StringIO()
    print_results(outcome_with_one_match(), ANSI, out=buffer)
    text = buffer.getvalue()
    assert f"{ANSI.bold}{ANSI.green}2023-11-08 12:00:05" in text
    assert f"{ANSI.blue}42{ANSI.reset}:" in text
    assert f"{ANSI.red}needle{ANSI.reset}" in text


def test_print_json_is_valid_ndjson():
    buffer = io.StringIO()
    print_json(outcome_with_one_match(), out=buffer)
    lines = buffer.getvalue().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["type"] == "match"
    assert record["timestamp"] == "20231108120005"
    assert record["datetime"] == "2023-11-08T12:00:05"
    assert record["line_number"] == 42
    assert record["spans"] == [[7, 13]]
    assert "id_" in record["raw_url"]


def test_summary_line_mentions_only_nonzero_buckets():
    stats = SearchStats(listed=10, scanned=8, matched=2, duplicates=2, errors=1)
    line = summary_line(stats, elapsed=1.23)
    assert "8 snapshot(s) scanned" in line
    assert "2 duplicate(s) skipped" in line
    assert "1 fetch error(s)" in line
    assert "binary" not in line
    assert "2 matched in 1.2s" in line
