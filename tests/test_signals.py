import numpy as np
import pandas as pd

from conftest import breakout_daily, daily_from_closes, et, intraday_day, orb_bars, pullback_daily
from tarayici import indicators as ind
from tarayici.signals import breakout_frame, completed_session_bars, daily_signals, orb_signal


# ---------------------------------------------------------------- indicators

def test_rsi_extremes():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert ind.rsi(up).iloc[-1] == 100
    flat = pd.Series(np.full(40, 5.0))
    assert ind.rsi(flat).iloc[-1] == 50
    assert ind.rsi(up).iloc[:13].isna().all()


def test_atr_constant_range():
    df = pd.DataFrame({"High": [11.0] * 30, "Low": [9.0] * 30, "Close": [10.0] * 30})
    assert abs(ind.atr(df).iloc[-1] - 2.0) < 1e-9


def test_prev_rolling_high_excludes_current_bar():
    h = pd.Series([1, 2, 3, 10], dtype=float)
    assert ind.prev_rolling_high(h, 3).iloc[-1] == 3


def test_no_lookahead_in_breakout_frame():
    df = breakout_daily()
    full = breakout_frame(df, {}, {})
    cut = breakout_frame(df.iloc[:-5], {}, {})
    pd.testing.assert_frame_equal(full.iloc[:-5], cut)


# ------------------------------------------------------------- daily setups

def test_breakout_signal(cfg):
    sigs = daily_signals("TEST", breakout_daily(), cfg)
    names = [s.strategy for s in sigs]
    assert "kirilim" in names
    s = next(x for x in sigs if x.strategy == "kirilim")
    assert s.stop < s.price < s.target1 < s.target
    assert abs((s.target - s.price) - 2 * (s.price - s.stop)) < 1e-9
    assert abs((s.target1 - s.price) - (s.price - s.stop)) < 1e-9


def test_breakout_needs_volume(cfg):
    df = breakout_daily()
    df.iloc[-1, df.columns.get_loc("Volume")] = 1_000_000
    assert "kirilim" not in [s.strategy for s in daily_signals("TEST", df, cfg)]


def test_breakout_disabled(cfg):
    cfg["stratejiler"]["kirilim"]["aktif"] = False
    assert "kirilim" not in [s.strategy for s in daily_signals("TEST", breakout_daily(), cfg)]


def test_pullback_signal(cfg):
    df = pullback_daily()
    r = ind.rsi(df["Close"])
    assert r.iloc[-2] < 40 <= r.iloc[-1], (r.iloc[-2], r.iloc[-1])
    assert "trend_geri_cekilme" in [s.strategy for s in daily_signals("TEST", df, cfg)]


def test_no_pullback_in_downtrend(cfg):
    closes = np.r_[np.linspace(120, 60, 250), [58, 56, 54, 53, 52, 51], 54]
    df = daily_from_closes(closes)
    assert "trend_geri_cekilme" not in [s.strategy for s in daily_signals("TEST", df, cfg)]


def test_too_little_history(cfg):
    assert daily_signals("TEST", breakout_daily().iloc[-100:], cfg) == []


# ------------------------------------------------------------------- ORB

def test_orb_fires_on_fresh_breakout(cfg):
    s = orb_signal("TEST", intraday_day(bars=orb_bars()), et(2026, 11, 10, 10, 0, 20), cfg)
    assert s is not None
    assert s.price == 101.6
    assert s.stop == 100.5  # middle of the 100.0–101.0 opening range
    assert abs(s.target - (101.6 + 2 * 1.1)) < 1e-9
    assert abs(s.target1 - (101.6 + 1.1)) < 1e-9


def test_orb_blocked_by_market_filter(cfg):
    cfg["filtreler"]["piyasa"]["aktif"] = True
    bars = intraday_day(bars=orb_bars())
    assert orb_signal("TEST", bars, et(2026, 11, 10, 10, 0, 20), cfg, market_ok=False) is None
    assert orb_signal("TEST", bars, et(2026, 11, 10, 10, 0, 20), cfg, market_ok=True) is not None


def test_orb_ignores_forming_bar(cfg):
    # at 09:59 the 09:55 bar is still open
    assert orb_signal("TEST", intraday_day(bars=orb_bars()), et(2026, 11, 10, 9, 59), cfg) is None


def test_orb_stale_breakout_not_reported(cfg):
    bars = orb_bars() + [(101.6, 101.9, 101.4, 101.7, 40_000)] * 5
    assert orb_signal("TEST", intraday_day(bars=bars), et(2026, 11, 10, 10, 25, 20), cfg) is None


def test_orb_needs_volume(cfg):
    bars = orb_bars()
    bars[-1] = (100.8, 101.8, 100.8, 101.6, 40_000)
    assert orb_signal("TEST", intraday_day(bars=bars), et(2026, 11, 10, 10, 0, 20), cfg) is None


def test_orb_cutoff_time(cfg):
    cfg["stratejiler"]["acilis_araligi_kirilimi"]["son_saat_et"] = "09:50"
    assert orb_signal("TEST", intraday_day(bars=orb_bars()), et(2026, 11, 10, 10, 0, 20), cfg) is None


def test_completed_session_bars_only_today():
    old = intraday_day(day=(2026, 11, 9), bars=orb_bars())
    new = intraday_day(day=(2026, 11, 10), bars=orb_bars())
    both = pd.concat([old, new])
    done = completed_session_bars(both, et(2026, 11, 10, 9, 41))
    assert len(done) == 2  # 09:30 and 09:35 bars of today
