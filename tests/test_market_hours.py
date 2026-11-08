from datetime import date, datetime, timezone

from conftest import et
from tarayici.market_hours import (holidays, is_early_close, is_trading_day, next_regular_open,
                                   previous_trading_day, session)


def test_holidays_2026():
    h = holidays(2026)
    assert date(2026, 1, 1) in h
    assert date(2026, 1, 19) in h      # MLK
    assert date(2026, 2, 16) in h      # Presidents
    assert date(2026, 4, 3) in h       # Good Friday (Easter Apr 5)
    assert date(2026, 5, 25) in h      # Memorial
    assert date(2026, 6, 19) in h      # Juneteenth (Fri)
    assert date(2026, 7, 3) in h       # July 4 is Saturday -> observed Friday
    assert date(2026, 9, 7) in h       # Labor
    assert date(2026, 11, 26) in h     # Thanksgiving
    assert date(2026, 12, 25) in h


def test_observed_rules():
    assert date(2027, 3, 26) in holidays(2027)    # Good Friday 2027
    assert date(2027, 6, 18) in holidays(2027)    # Juneteenth Sat -> Fri
    assert date(2027, 12, 24) in holidays(2027)   # Christmas Sat -> Fri
    # Jan 1 2028 is a Saturday: no Friday closure on Dec 31 2027
    assert is_trading_day(date(2027, 12, 31))
    assert date(2027, 12, 31) not in holidays(2027) | holidays(2028)


def test_early_close():
    assert is_early_close(date(2026, 11, 27))     # day after Thanksgiving
    assert is_early_close(date(2026, 12, 24))
    assert not is_early_close(date(2026, 11, 10))
    assert session(et(2026, 11, 27, 13, 30)) == "post"


def test_sessions():
    assert session(et(2026, 11, 10, 3, 59)) == "closed"
    assert session(et(2026, 11, 10, 4, 0)) == "pre"
    assert session(et(2026, 11, 10, 9, 30)) == "regular"
    assert session(et(2026, 11, 10, 15, 59)) == "regular"
    assert session(et(2026, 11, 10, 16, 0)) == "post"
    assert session(et(2026, 11, 14, 12, 0)) == "closed"   # Saturday
    assert session(et(2026, 11, 26, 12, 0)) == "closed"   # Thanksgiving


def test_dst_is_handled():
    # 14:30 UTC is 09:30 ET in winter (EST) but 10:30 ET in summer (EDT)
    assert session(datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)) == "regular"
    assert session(datetime(2026, 11, 10, 14, 29, tzinfo=timezone.utc)) == "pre"
    assert session(datetime(2026, 7, 7, 13, 30, tzinfo=timezone.utc)) == "regular"


def test_next_open_and_previous_day():
    assert next_regular_open(et(2026, 11, 13, 18, 0)) == et(2026, 11, 16, 9, 30)  # Fri -> Mon
    assert next_regular_open(et(2026, 11, 10, 8, 0)) == et(2026, 11, 10, 9, 30)
    assert next_regular_open(et(2026, 11, 25, 17, 0)) == et(2026, 11, 27, 9, 30)  # skip Thanksgiving
    assert previous_trading_day(date(2026, 11, 16)) == date(2026, 11, 13)
