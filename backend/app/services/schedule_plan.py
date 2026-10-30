"""Generates the sample schedule. The pack has event durations and venues but no times.

Places the most constrained events first, then picks the slot with the lowest cost.
Participants who would miss the event, through a clash or by not being on campus,
weigh more than seats short. See ASSUMPTIONS #5, #6 and #7.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

TURNAROUND = timedelta(minutes=30)
ROUND_GAP = timedelta(minutes=15)
GRID = timedelta(minutes=30)
MISSED_WEIGHT = 3

SUITABLE_VENUES = {
    "open ground": {"Amphitheatre", "Quadrangle", "Hostel Lawn"},
    "quiet room": {"Library Hall", "Seminar Hall"},
    "stage; lights": {"Main Stage", "Amphitheatre"},
    "audio; seating": {"LT-1", "LT-2", "Seminar Hall"},
    "projector; seating": {"LT-1", "LT-2", "Seminar Hall"},
    "power; tables": {"Sports Complex", "Seminar Hall"},
}


@dataclass(frozen=True)
class PlanEvent:
    code: str
    requirement: str
    length: timedelta
    participants: frozenset[str]
    stays: tuple[tuple[datetime, datetime], ...] = ()  # arrival and departure of each registrant with valid dates


@dataclass(frozen=True)
class PlanVenue:
    code: str
    name: str
    capacity: int
    windows: tuple[tuple[datetime, datetime], ...]


@dataclass(frozen=True)
class Slot:
    event: PlanEvent
    venue: PlanVenue
    start: datetime
    end: datetime


def event_length(rounds: int, duration_minutes: int) -> timedelta:
    return timedelta(minutes=rounds * duration_minutes) + (rounds - 1) * ROUND_GAP


def build_schedule(events: list[PlanEvent], venues: list[PlanVenue]) -> list[Slot]:
    candidates = {e.code: [v for v in venues if v.name in SUITABLE_VENUES[e.requirement]] for e in events}
    placed: list[Slot] = []

    for event in sorted(events, key=lambda e: (len(candidates[e.code]), -e.length, e.code)):
        best: tuple[tuple[int, datetime, str], Slot] | None = None
        for venue in candidates[event.code]:
            for window_start, window_end in venue.windows:
                start = window_start
                while start + event.length <= window_end:
                    end = start + event.length
                    if _venue_free(placed, venue, start, end):
                        clashes = sum(len(event.participants & s.event.participants)
                                      for s in placed if start < s.end and s.start < end)
                        away = sum(1 for arrive, leave in event.stays if arrive > start or leave < end)
                        short = max(0, len(event.participants) - venue.capacity)
                        key = ((clashes + away) * MISSED_WEIGHT + short, start, venue.code)
                        if best is None or key < best[0]:
                            best = (key, Slot(event, venue, start, end))
                    start += GRID
        if best is None:
            raise ValueError(f"No slot fits {event.code}")
        placed.append(best[1])
    return sorted(placed, key=lambda s: (s.start, s.venue.code))


def _venue_free(placed: list[Slot], venue: PlanVenue, start: datetime, end: datetime) -> bool:
    return all(not (start < s.end + TURNAROUND and s.start < end + TURNAROUND) for s in placed if s.venue == venue)
