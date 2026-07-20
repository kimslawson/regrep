import io

from conftest import make_snapshot
from regrep.core import LineMatch, SnapshotPresence
from regrep.output import ANSI, PLAIN, print_timeline, print_timeline_json
from regrep.timeline import (
    build_timeline,
    ever_present,
    first_appearance,
)


def pres(ts: str, present: bool, sample_text: str | None = None) -> SnapshotPresence:
    sample = None
    if sample_text is not None:
        sample = LineMatch(1, sample_text, ((0, len(sample_text)),))
    return SnapshotPresence(make_snapshot(ts, digest=ts), present, sample)


def test_empty_presence_yields_no_segments():
    assert build_timeline([]) == []
    assert first_appearance([]) is None
    assert ever_present([]) is False


def test_all_present_is_one_segment():
    segs = build_timeline([pres(f"2020010100000{i}", True, "needle") for i in range(3)])
    assert len(segs) == 1
    seg = segs[0]
    assert seg.present is True
    assert seg.count == 3
    assert seg.prev_end is None
    assert seg.is_transition is False
    assert seg.start.timestamp == "20200101000000"
    assert seg.end.timestamp == "20200101000002"


def test_appear_then_vanish():
    segs = build_timeline(
        [
            pres("20180101000000", False),
            pres("20190101000000", True, "the needle"),
            pres("20200101000000", True, "the needle again"),
            pres("20210101000000", False),
        ]
    )
    assert [(s.present, s.count) for s in segs] == [(False, 1), (True, 2), (False, 1)]

    appeared = segs[1]
    assert appeared.is_transition is True
    assert appeared.prev_end.timestamp == "20180101000000"  # last absent before it
    assert appeared.start.timestamp == "20190101000000"
    assert appeared.sample.text == "the needle"  # first matching line of the run

    vanished = segs[2]
    assert vanished.prev_end.timestamp == "20200101000000"  # last present before gone
    assert vanished.start.timestamp == "20210101000000"


def test_reappearance_is_kept_as_distinct_segments():
    # present -> absent -> present again; timeline must show all three.
    segs = build_timeline(
        [
            pres("20200101000000", True, "needle"),
            pres("20210101000000", False),
            pres("20220101000000", True, "needle"),
        ]
    )
    assert [s.present for s in segs] == [True, False, True]
    assert segs[2].is_transition is True
    assert segs[2].prev_end.timestamp == "20210101000000"
    assert first_appearance(segs).start.timestamp == "20200101000000"
    assert ever_present(segs) is True


def test_never_present():
    segs = build_timeline([pres("20200101000000", False), pres("20210101000000", False)])
    assert len(segs) == 1
    assert ever_present(segs) is False
    assert first_appearance(segs) is None


def test_print_timeline_plain_layout():
    segs = build_timeline(
        [
            pres("20180101000000", False),
            pres("20190314072241", True, "deprecated_function() removed soon"),
            pres("20210602180519", False),
        ]
    )
    buffer = io.StringIO()
    print_timeline(segs, PLAIN, out=buffer)
    text = buffer.getvalue()
    assert "http://example.com/page" in text
    assert "+ present" in text
    assert "- absent" in text
    assert "appeared between 2018-01-01 00:00:00 (absent) and 2019-03-14 07:22:41" in text
    assert "vanished between 2019-03-14 07:22:41 (present) and 2021-06-02 18:05:19" in text
    assert "deprecated_function() removed soon" in text
    assert "→ absent as of 2021-06-02 18:05:19" in text
    assert "\033[" not in text


def test_print_timeline_first_segment_predates_note():
    segs = build_timeline([pres("20190314072241", True, "needle")])
    buffer = io.StringIO()
    print_timeline(segs, PLAIN, out=buffer)
    note = "present at the earliest snapshot (2019-03-14 07:22:41); may predate"
    assert note in buffer.getvalue()


def test_print_timeline_ansi_colors_sample_match():
    segs = build_timeline([pres("20190314072241", True, "needle")])
    buffer = io.StringIO()
    print_timeline(segs, ANSI, out=buffer)
    text = buffer.getvalue()
    assert f"{ANSI.red}needle{ANSI.reset}" in text
    assert ANSI.green in text


def test_print_timeline_empty_is_silent():
    buffer = io.StringIO()
    print_timeline([], PLAIN, out=buffer)
    assert buffer.getvalue() == ""


def test_print_timeline_json_records():
    import json

    segs = build_timeline(
        [
            pres("20180101000000", False),
            pres("20190314072241", True, "the needle"),
        ]
    )
    buffer = io.StringIO()
    print_timeline_json(segs, out=buffer)
    records = [json.loads(line) for line in buffer.getvalue().splitlines()]
    assert len(records) == 2
    assert records[0]["present"] is False
    assert records[0]["sample_line"] is None
    assert records[0]["changed_after_timestamp"] is None
    assert records[1]["present"] is True
    assert records[1]["sample_line"] == "the needle"
    assert records[1]["changed_after_timestamp"] == "20180101000000"
    assert records[1]["start_datetime"] == "2019-03-14T07:22:41"
    assert records[1]["snapshot_count"] == 1
