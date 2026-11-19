"""Contact-hour windows in a given time zone (buyer local hours, manager alert hours)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ISO_WEEKDAYS_ALL = frozenset(range(1, 8))
_LANGUAGE_TAG: Final = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})*")


@lru_cache(maxsize=512)
def valid_timezone(name: str) -> bool:
    """L8 (security review 19 Nov): an IANA zone name ZoneInfo can load ("Asia/Dubai"). A stored "Not/AZone"
    used to break every follow-up of that contact."""
    if not name or len(name) > 64 or name.startswith(("/", ".")) or ".." in name:
        return False
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError, OSError:
        return False
    return True


def language_tag(value: str) -> str | None:
    """L8: a BCP 47-shaped language tag in lower case ("en", "ar", "zh-hans"), or None when it is not one."""
    tag = value.strip().replace("_", "-").lower()
    return tag if len(tag) <= 16 and _LANGUAGE_TAG.fullmatch(tag) else None


@dataclass(frozen=True, slots=True)
class Window:
    start: time
    end: time
    tz: str
    weekdays: frozenset[int] = ISO_WEEKDAYS_ALL  # ISO 1=Mon .. 7=Sun

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("window end must be after start (overnight windows are not supported)")

    @classmethod
    def parse(cls, start: str, end: str, tz: str, weekdays: frozenset[int] = ISO_WEEKDAYS_ALL) -> Window:
        return cls(time.fromisoformat(start), time.fromisoformat(end), tz, weekdays)

    def contains(self, at: datetime) -> bool:
        local = _aware(at).astimezone(ZoneInfo(self.tz))
        return local.isoweekday() in self.weekdays and self.start <= local.time() < self.end

    def next_open(self, at: datetime) -> datetime:
        """`at` itself if inside the window, else the next window start (returned in UTC)."""
        at = _aware(at)
        if self.contains(at):
            return at.astimezone(UTC)
        zone = ZoneInfo(self.tz)
        local = at.astimezone(zone)
        for day in range(8):
            candidate_date = local.date() + timedelta(days=day)
            candidate = datetime.combine(candidate_date, self.start, tzinfo=zone)
            if candidate.isoweekday() in self.weekdays and candidate > local:
                return candidate.astimezone(UTC)
        raise ValueError("window has no open weekday")  # pragma: no cover

    def add_open_minutes(self, at: datetime, minutes: int) -> datetime:
        """Add `minutes` counting only time inside the window (the approval timer pauses outside)."""
        remaining = timedelta(minutes=minutes)
        cursor = self.next_open(at)
        zone = ZoneInfo(self.tz)
        while True:
            local = cursor.astimezone(zone)
            closes = datetime.combine(local.date(), self.end, tzinfo=zone)
            available = closes - local
            if remaining <= available:
                return (local + remaining).astimezone(UTC)
            remaining -= available
            cursor = self.next_open(closes)


def _aware(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("naive datetime: pass an aware datetime (UTC)")
    return at
