"""Entry setups.

Daily strategies are written as vectorised functions over the whole price
history, so the live scanner (which looks at the last bar) and the backtest
(which looks at every bar) run exactly the same code.

These are classic, transparent technical setups. They are not a proven edge:
run geriye_test.py and watch the live track record before trusting them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

import pandas as pd

from . import indicators as ind
from .market_hours import ET, close_time, regular_open_dt

STRATEGY_NAMES = {
    "kirilim": "Kırılım (günlük)",
    "trend_geri_cekilme": "Trendde geri çekilme (günlük)",
    "acilis_araligi_kirilimi": "Açılış aralığı kırılımı (5 dk)",
}

MIN_DAILY_BARS = 210  # 200-day average + warm-up


@dataclass(frozen=True)
class Signal:
    ticker: str
    strategy: str
    timeframe: str  # "daily" or "intraday"
    bar_time: pd.Timestamp
    price: float
    stop: float
    target: float
    reasons: tuple[str, ...]

    @property
    def name(self) -> str:
        return STRATEGY_NAMES.get(self.strategy, self.strategy)

    @property
    def key(self) -> str:
        return f"{self.ticker}:{self.strategy}"

    @property
    def risk_pct(self) -> float:
        return (self.price - self.stop) / self.price * 100

    @property
    def reward_pct(self) -> float:
        return (self.target - self.price) / self.price * 100


# ---------------------------------------------------------------- daily

def breakout_frame(df: pd.DataFrame, p: dict, risk: dict) -> pd.DataFrame:
    """Fresh close above the prior N-day high, on heavy volume, above the 50-day average."""
    c = df["Close"]
    n = int(p.get("zirve_gun", 20))
    k = float(p.get("hacim_kati", 1.5))
    prev_high = ind.prev_rolling_high(df["High"], n)
    vol_ratio = df["Volume"] / ind.prev_rolling_mean(df["Volume"], 20)
    s50 = ind.sma(c, 50)
    a = ind.atr(df, 14)
    above = (c > prev_high).fillna(False)
    fresh = above & ~above.shift(1, fill_value=False)
    signal = fresh & (vol_ratio >= k) & (c > s50) & a.notna()
    stop = c - float(risk.get("atr_carpani", 2.0)) * a
    target = c + float(risk.get("hedef_r", 2.0)) * (c - stop)
    return pd.DataFrame(
        {"signal": signal.astype(bool), "stop": stop, "target": target,
         "prev_high": prev_high, "vol_ratio": vol_ratio, "n": n},
        index=df.index,
    )


def breakout_reasons(row: pd.Series) -> tuple[str, ...]:
    return (
        f"{int(row['n'])} günlük zirve ({row['prev_high']:.2f} $) kapanışla kırıldı",
        f"Hacim 20 günlük ortalamanın {row['vol_ratio']:.1f} katı",
        "Fiyat 50 günlük ortalamanın üstünde",
    )


def pullback_frame(df: pd.DataFrame, p: dict, risk: dict) -> pd.DataFrame:
    """Uptrend (price > 200d, 50d > 200d) and RSI(14) crossing back up through a threshold."""
    c = df["Close"]
    level = float(p.get("rsi_esik", 40))
    s50, s200 = ind.sma(c, 50), ind.sma(c, 200)
    r = ind.rsi(c, 14)
    a = ind.atr(df, 14)
    cross = (r.shift(1) < level) & (r >= level)
    signal = (cross & (c > s200) & (s50 > s200) & a.notna()).fillna(False)
    stop = c - float(risk.get("atr_carpani", 2.0)) * a
    target = c + float(risk.get("hedef_r", 2.0)) * (c - stop)
    return pd.DataFrame(
        {"signal": signal.astype(bool), "stop": stop, "target": target,
         "rsi_prev": r.shift(1), "rsi": r, "level": level},
        index=df.index,
    )


def pullback_reasons(row: pd.Series) -> tuple[str, ...]:
    return (
        "Ana trend yukarı (fiyat 200 günlük ortalamanın üstünde, 50G > 200G)",
        f"RSI {row['rsi_prev']:.0f} → {row['rsi']:.0f}: {row['level']:.0f} seviyesini yukarı kesti, "
        "geri çekilme bitiyor olabilir",
    )


DAILY_STRATEGIES = {
    "kirilim": (breakout_frame, breakout_reasons),
    "trend_geri_cekilme": (pullback_frame, pullback_reasons),
}


def daily_signals(ticker: str, df: pd.DataFrame, cfg: dict) -> list[Signal]:
    """Signals on the LAST bar of df. df must contain completed daily bars only."""
    if df is None or len(df) < MIN_DAILY_BARS:
        return []
    out = []
    for key, (frame_fn, reasons_fn) in DAILY_STRATEGIES.items():
        p = cfg["stratejiler"].get(key, {})
        if not p.get("aktif", True):
            continue
        fr = frame_fn(df, p, cfg["risk"])
        last = fr.iloc[-1]
        if not bool(last["signal"]):
            continue
        price = float(df["Close"].iloc[-1])
        if not (last["stop"] < price < last["target"]):
            continue
        out.append(Signal(ticker, key, "daily", df.index[-1], price,
                          float(last["stop"]), float(last["target"]), reasons_fn(last)))
    return out


# ------------------------------------------------------------- intraday

def completed_session_bars(bars: pd.DataFrame, now: datetime, bar_minutes: int = 5) -> pd.DataFrame:
    """Today's regular-session bars that have fully closed by `now`.

    Bar timestamps are bar START times (Yahoo convention). A bar is complete
    when start + bar length <= now; the still-forming bar is dropped so we
    never act on a candle that can still change.
    """
    if bars is None or bars.empty:
        return bars
    idx = bars.index
    if idx.tz is None:
        idx = idx.tz_localize(ET)
    else:
        idx = idx.tz_convert(ET)
    b = bars.set_axis(idx)
    today = now.astimezone(ET).date()
    start = regular_open_dt(today)
    end = datetime.combine(today, close_time(today), tzinfo=ET)
    width = timedelta(minutes=bar_minutes)
    mask = (b.index >= start) & (b.index < end) & (b.index + width <= now)
    return b[mask]


def _parse_hhmm(s: str) -> time:
    h, m = str(s).split(":")
    return time(int(h), int(m))


def orb_signal(ticker: str, bars: pd.DataFrame, now: datetime, cfg: dict) -> Signal | None:
    """Opening range breakout on 5-minute bars.

    Opening range = first N minutes after 09:30 ET. Signal when a bar closes
    above the range high for the first time, on above-average volume, above
    VWAP, before the cut-off time. Only fresh breakouts (bar closed within the
    last `taze_dakika` minutes) are reported, so a late scan does not send a
    stale alert.
    """
    p = cfg["stratejiler"].get("acilis_araligi_kirilimi", {})
    if not p.get("aktif", True):
        return None
    bar_min = int(cfg["zamanlama"].get("bar_dakika", 5))
    b = completed_session_bars(bars, now, bar_min)
    if b is None or b.empty:
        return None
    today = now.astimezone(ET).date()
    rng = int(p.get("aralik_dakika", 15))
    or_end = regular_open_dt(today) + timedelta(minutes=rng)
    or_bars = b[b.index < or_end]
    if len(or_bars) < max(1, rng // bar_min):
        return None
    after = b[b.index >= or_end]
    if after.empty:
        return None
    or_high, or_low = float(or_bars["High"].max()), float(or_bars["Low"].min())
    above = after["Close"] > or_high
    if not above.any():
        return None
    first_ts = above.idxmax()
    fresh_min = float(p.get("taze_dakika", 15))
    if first_ts + timedelta(minutes=bar_min) < now - timedelta(minutes=fresh_min):
        return None  # breakout bar closed too long ago; too late to alert
    if first_ts.time() > _parse_hhmm(p.get("son_saat_et", "11:30")):
        return None
    last_close = float(after["Close"].iloc[-1])
    if last_close <= or_high:
        return None
    prior = b[b.index < first_ts]
    vol_ratio = float(after.loc[first_ts, "Volume"]) / max(float(prior["Volume"].mean()), 1.0)
    if vol_ratio < float(p.get("hacim_kati", 1.5)):
        return None
    vwap = float(ind.session_vwap(b).iloc[-1])
    if last_close <= vwap:
        return None
    stop = (or_high + or_low) / 2
    if stop >= last_close:
        return None
    target = last_close + float(cfg["risk"].get("hedef_r", 2.0)) * (last_close - stop)
    reasons = (
        f"İlk {rng} dakikanın aralığı {or_low:.2f}–{or_high:.2f} $; üst sınır kapanışla kırıldı",
        f"Kırılım mumunun hacmi günün ortalamasının {vol_ratio:.1f} katı",
        f"Fiyat VWAP'ın ({vwap:.2f} $) üstünde",
    )
    return Signal(ticker, "acilis_araligi_kirilimi", "intraday", after.index[-1],
                  last_close, stop, target, reasons)
