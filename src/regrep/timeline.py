"""Timeline / diff mode: when did a phrase appear and vanish across snapshots?

Turns the per-snapshot present/absent record from a search into a list of
alternating segments. Each segment knows the last snapshot of the preceding
(opposite) state, so a change can be honestly bracketed: we rarely know the
exact moment a phrase appeared, only that it happened *between* the last
capture without it and the first capture with it.
"""

from dataclasses import dataclass, replace

from .core import LineMatch, SnapshotPresence
from .providers.base import Snapshot


@dataclass(frozen=True)
class TimelineSegment:
    present: bool  # was the pattern present throughout this run?
    start: Snapshot  # first snapshot in the run
    end: Snapshot  # last snapshot in the run
    count: int  # number of fetched snapshots in the run
    prev_end: Snapshot | None  # last snapshot of the preceding opposite run
    sample: LineMatch | None  # a representative matching line (present runs)

    @property
    def is_transition(self) -> bool:
        """True when this run follows an opposite run (a real appear/vanish)."""
        return self.prev_end is not None


def build_timeline(presence: list[SnapshotPresence]) -> list[TimelineSegment]:
    """Collapse a chronological presence list into present/absent segments.

    `presence` must already be sorted oldest-first (as `run_search` returns
    it). Consecutive snapshots sharing a state are merged; the boundary
    between two segments is the transition point.
    """
    segments: list[TimelineSegment] = []
    for record in presence:
        if segments and segments[-1].present == record.present:
            current = segments[-1]
            segments[-1] = replace(
                current,
                end=record.snapshot,
                count=current.count + 1,
                sample=current.sample or record.sample,
            )
        else:
            prev_end = segments[-1].end if segments else None
            segments.append(
                TimelineSegment(
                    present=record.present,
                    start=record.snapshot,
                    end=record.snapshot,
                    count=1,
                    prev_end=prev_end,
                    sample=record.sample,
                )
            )
    return segments


def first_appearance(segments: list[TimelineSegment]) -> TimelineSegment | None:
    """The earliest present segment, if the phrase was ever seen."""
    return next((seg for seg in segments if seg.present), None)


def ever_present(segments: list[TimelineSegment]) -> bool:
    return any(seg.present for seg in segments)
