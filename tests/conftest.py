import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tarayici.config import DEFAULTS, _merge  # noqa: E402
from tarayici.market_hours import ET  # noqa: E402


def et(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET)


def daily_from_closes(closes, end="2026-11-09", vol=1_000_000, spread=0.005):
    closes = np.asarray(closes, float)
    idx = pd.bdate_range(end=end, periods=len(closes))
    opens = np.r_[closes[0], closes[:-1]]
    high = np.maximum(opens, closes) * (1 + spread)
    low = np.minimum(opens, closes) * (1 - spread)
    v = np.full(len(closes), float(vol))
    return pd.DataFrame({"Open": opens, "High": high, "Low": low, "Close": closes, "Volume": v}, index=idx)


def breakout_daily(end="2026-11-09"):
    """Steady uptrend, 25-day sideways range under ~100, then a high-volume breakout."""
    rng = np.random.default_rng(1)
    up = np.linspace(50, 98, 230) + rng.normal(0, 0.2, 230)
    side = 98 + 1.5 * np.sin(np.arange(25))  # 96.5 .. 99.5
    closes = np.r_[up, side, 103.0]
    df = daily_from_closes(closes, end=end)
    df.iloc[-1, df.columns.get_loc("Volume")] = 3_000_000
    return df


def pullback_daily(end="2026-11-09"):
    """Long uptrend, a sharp multi-day dip that pushes RSI under 40, then a rebound day."""
    up = np.linspace(50, 120, 250)
    dip = [118, 115.5, 113, 111, 109.5, 108.5]
    closes = np.r_[up, dip, 113.5]
    return daily_from_closes(closes, end=end)


def intraday_day(day=(2026, 11, 10), bars=None):
    """5-minute bars starting 09:30 ET. bars = list of (open, high, low, close, volume)."""
    start = pd.Timestamp(datetime(*day, 9, 30), tz=ET)
    idx = pd.date_range(start, periods=len(bars), freq="5min")
    return pd.DataFrame(bars, columns=["Open", "High", "Low", "Close", "Volume"], index=idx)


def orb_bars():
    return [
        (100.2, 101.0, 100.0, 100.5, 50_000),   # 09:30  opening range
        (100.5, 100.9, 100.1, 100.4, 40_000),   # 09:35
        (100.4, 100.8, 100.2, 100.6, 40_000),   # 09:40
        (100.6, 100.9, 100.3, 100.7, 30_000),   # 09:45
        (100.7, 100.95, 100.5, 100.8, 30_000),  # 09:50
        (100.8, 101.8, 100.8, 101.6, 150_000),  # 09:55  breakout bar
    ]


@pytest.fixture
def cfg():
    c = _merge(DEFAULTS, {})
    c["izleme_listesi"] = ["TEST"]
    return c


class FakeSource:
    def __init__(self, daily=None, intraday=None):
        self.daily_data = daily or {}
        self.intraday_data = intraday or {}
        self.calls = {"daily": 0, "intraday": 0}

    def daily(self, tickers):
        self.calls["daily"] += 1
        return {t: df for t, df in self.daily_data.items() if t in tickers}

    def intraday(self, tickers):
        self.calls["intraday"] += 1
        return {t: df for t, df in self.intraday_data.items() if t in tickers}


class FakeNotifier:
    def __init__(self, ok=True):
        self.messages = []
        self.ok = ok

    def send(self, text):
        self.messages.append(text)
        return self.ok
