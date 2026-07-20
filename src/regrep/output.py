"""Terminal and JSON output, ripgrep-flavored.

Color scheme (per the original brief): matches red, source + datestamp
green, line numbers blue. Colors are only emitted when the destination is
a terminal (or forced with --color always) and NO_COLOR is unset.
"""

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import IO

from .core import SearchOutcome, SearchStats
from .timeline import TimelineSegment

ELLIPSIS = "…"


@dataclass(frozen=True)
class Palette:
    red: str = ""
    green: str = ""
    blue: str = ""
    bold: str = ""
    dim: str = ""
    reset: str = ""


ANSI = Palette(
    red="\033[31m",
    green="\033[32m",
    blue="\033[34m",
    bold="\033[1m",
    dim="\033[2m",
    reset="\033[0m",
)
PLAIN = Palette()


def make_palette(mode: str, stream: IO) -> Palette:
    if mode == "always":
        return ANSI
    if mode == "never":
        return PLAIN
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return PLAIN
    return ANSI if getattr(stream, "isatty", lambda: False)() else PLAIN


def colorize_spans(text: str, spans: Sequence[tuple[int, int]], palette: Palette) -> str:
    """Wrap each (start, end) span of `text` in match coloring."""
    if not spans or not palette.red:
        return text
    parts = []
    last = 0
    for start, end in spans:
        start = max(start, last)  # guard against overlap
        if end <= start:  # zero-width matches have nothing to paint
            continue
        parts.append(text[last:start])
        parts.append(f"{palette.red}{text[start:end]}{palette.reset}")
        last = end
    parts.append(text[last:])
    return "".join(parts)


def render_line(
    line: str,
    spans: Sequence[tuple[int, int]],
    palette: Palette,
    max_columns: int,
) -> str:
    """Color the matches; if the line is too long, show a window around the
    first match instead of flooding the terminal (minified HTML is often a
    single hundreds-of-KB line)."""
    if max_columns and len(line) > max_columns:
        anchor = spans[0][0] if spans else 0
        start = max(0, min(anchor - max_columns // 4, len(line) - max_columns))
        end = start + max_columns
        clipped = [
            (max(s, start) - start, min(e, end) - start)
            for s, e in spans
            if s < end and e > start
        ]
        prefix = ELLIPSIS if start > 0 else ""
        suffix = ELLIPSIS if end < len(line) else ""
        return prefix + colorize_spans(line[start:end], clipped, palette) + suffix
    return colorize_spans(line, spans, palette)


def print_results(
    outcome: SearchOutcome,
    palette: Palette,
    out: IO | None = None,
    max_columns: int = 200,
) -> None:
    out = out if out is not None else sys.stdout
    for index, result in enumerate(outcome.results):
        if index:
            print(file=out)
        snap = result.snapshot
        print(
            f"{palette.bold}{palette.green}{snap.date_display}  {snap.original_url}"
            f"{palette.reset}",
            file=out,
        )
        print(f"{palette.dim}{snap.replay_url}{palette.reset}", file=out)
        for match in result.lines:
            rendered = render_line(match.text.rstrip(), match.spans, palette, max_columns)
            print(f"{palette.blue}{match.number}{palette.reset}:{rendered}", file=out)


def print_json(outcome: SearchOutcome, out: IO | None = None) -> None:
    """One JSON object per matching line (NDJSON), for piping into jq etc."""
    out = out if out is not None else sys.stdout
    for result in outcome.results:
        snap = result.snapshot
        for match in result.lines:
            dt = snap.dt
            record = {
                "type": "match",
                "provider": snap.provider,
                "timestamp": snap.timestamp,
                "datetime": dt.isoformat() if dt else None,
                "original_url": snap.original_url,
                "raw_url": snap.raw_url,
                "replay_url": snap.replay_url,
                "line_number": match.number,
                "line": match.text,
                "spans": [list(span) for span in match.spans],
            }
            print(json.dumps(record, ensure_ascii=False), file=out)


def _bracket_note(segment: TimelineSegment) -> str:
    """One line explaining when/how this segment's state began."""
    if segment.prev_end is None:
        if segment.present:
            return (
                f"present at the earliest snapshot ({segment.start.date_display}); "
                "may predate the archive"
            )
        return f"absent at the earliest snapshot ({segment.start.date_display})"
    verb = "appeared" if segment.present else "vanished"
    prior = "absent" if segment.present else "present"
    return (
        f"{verb} between {segment.prev_end.date_display} ({prior}) "
        f"and {segment.start.date_display}"
    )


def _print_segment(
    segment: TimelineSegment, palette: Palette, out: IO, max_columns: int
) -> None:
    if segment.present:
        sign, color, label = "+", palette.green, "present"
    else:
        sign, color, label = "-", palette.dim, "absent"
    span = segment.start.date_display
    if segment.end.timestamp != segment.start.timestamp:
        span += f" → {segment.end.date_display}"
    count = f"{segment.count} snapshot" + ("s" if segment.count != 1 else "")
    print(
        f"{color}{sign} {label:<7}{palette.reset} {color}{span}{palette.reset}"
        f"  {palette.blue}({count}){palette.reset}",
        file=out,
    )
    print(f"{palette.dim}    {_bracket_note(segment)}{palette.reset}", file=out)
    if segment.present and segment.sample is not None:
        rendered = render_line(
            segment.sample.text.rstrip(), segment.sample.spans, palette, max_columns
        )
        print(f"    {palette.blue}{segment.sample.number}{palette.reset}:{rendered}", file=out)


def print_timeline(
    segments: list[TimelineSegment],
    palette: Palette,
    out: IO | None = None,
    max_columns: int = 200,
) -> None:
    out = out if out is not None else sys.stdout
    if not segments:
        return
    url = segments[0].start.original_url
    print(f"{palette.bold}{palette.green}{url}{palette.reset}", file=out)
    for segment in segments:
        _print_segment(segment, palette, out, max_columns)
    last = segments[-1]
    state = "present" if last.present else "absent"
    print(
        f"{palette.dim}  → {state} as of {last.end.date_display}, "
        f"the most recent snapshot checked{palette.reset}",
        file=out,
    )


def print_timeline_json(segments: list[TimelineSegment], out: IO | None = None) -> None:
    """One JSON object per timeline segment (NDJSON)."""
    out = out if out is not None else sys.stdout
    for segment in segments:
        start, end = segment.start, segment.end
        record = {
            "type": "timeline-segment",
            "present": segment.present,
            "provider": start.provider,
            "original_url": start.original_url,
            "start_timestamp": start.timestamp,
            "end_timestamp": end.timestamp,
            "start_datetime": start.dt.isoformat() if start.dt else None,
            "end_datetime": end.dt.isoformat() if end.dt else None,
            "snapshot_count": segment.count,
            "start_replay_url": start.replay_url,
            "changed_after_timestamp": segment.prev_end.timestamp if segment.prev_end else None,
            "sample_line": segment.sample.text if (segment.present and segment.sample) else None,
        }
        print(json.dumps(record, ensure_ascii=False), file=out)


def summary_line(stats: SearchStats, elapsed: float) -> str:
    parts = [f"{stats.scanned} snapshot(s) scanned"]
    if stats.duplicates:
        parts.append(f"{stats.duplicates} duplicate(s) skipped")
    if stats.non_text:
        parts.append(f"{stats.non_text} non-text skipped")
    if stats.binary:
        parts.append(f"{stats.binary} binary skipped")
    if stats.too_large:
        parts.append(f"{stats.too_large} over size limit")
    if stats.errors:
        parts.append(f"{stats.errors} fetch error(s)")
    return f"[regrep] {', '.join(parts)}; {stats.matched} matched in {elapsed:.1f}s"
