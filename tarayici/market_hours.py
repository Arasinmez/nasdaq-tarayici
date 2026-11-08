"""US equity market calendar (Nasdaq/NYSE share the same holidays).

All logic is in America/New_York time, so US daylight-saving changes are
handled automatically. Holidays are computed by rule; early closes follow the
usual pattern (day after Thanksgiving, Christmas Eve, July 3) and should be
treated as a close approximation, not an official calendar.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

PRE_OPEN = time(4, 0)
REG_OPEN = time(9, 30)
REG_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
POST_CLOSE = time(20, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d: date) -> date:
    if d.weekday() == 5:  # Saturday -> Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> Monday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset[date]:
    h: set[date] = set()
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        h.add(date(year, 1, 2))
    elif new_year.weekday() < 5:
        h.add(new_year)
    # If Jan 1 is a Saturday the exchange does NOT close on Friday Dec 31.
    h.add(_nth_weekday(year, 1, 0, 3))   # Martin Luther King Jr. Day
    h.add(_nth_weekday(year, 2, 0, 3))   # Presidents' Day
    h.add(_easter(year) - timedelta(days=2))  # Good Friday
    h.add(_last_weekday(year, 5, 0))     # Memorial Day
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))  # Juneteenth
    h.add(_observed(date(year, 7, 4)))   # Independence Day
    h.add(_nth_weekday(year, 9, 0, 1))   # Labor Day
    h.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    h.add(_observed(date(year, 12, 25)))  # Christmas
    return frozenset(h)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def is_early_close(d: date) -> bool:
    if not is_trading_day(d):
        return False
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True  # day after Thanksgiving
    if d.month == 12 and d.day == 24:
        return True
    if d.month == 7 and d.day == 3 and d.weekday() <= 3:
        return True
    return False


def close_time(d: date) -> time:
    return EARLY_CLOSE if is_early_close(d) else REG_CLOSE


def to_et(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return now.astimezone(ET)


def session(now: datetime) -> str:
    """Return 'pre', 'regular', 'post' or 'closed' for the given moment."""
    n = to_et(now)
    d, t = n.date(), n.time()
    if not is_trading_day(d):
        return "closed"
    close = close_time(d)
    if REG_OPEN <= t < close:
        return "regular"
    if PRE_OPEN <= t < REG_OPEN:
        return "pre"
    if close <= t < POST_CLOSE:
        return "post"
    return "closed"


def regular_open_dt(d: date) -> datetime:
    return datetime.combine(d, REG_OPEN, tzinfo=ET)


def regular_close_dt(d: date) -> datetime:
    return datetime.combine(d, close_time(d), tzinfo=ET)


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_regular_open(now: datetime) -> datetime:
    n = to_et(now)
    d = n.date()
    if is_trading_day(d) and n < regular_open_dt(d):
        return regular_open_dt(d)
    return regular_open_dt(next_trading_day(d))
