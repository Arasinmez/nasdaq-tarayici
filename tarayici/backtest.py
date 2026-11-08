"""Historical test of the daily strategies, using the same signal code as live.

Rules: enter at the next day's open (plus slippage), keep the signal's stop
and target, exit at whichever is touched first. If one day touches both, the
stop is assumed (conservative). Otherwise exit at the close after
`max_hold` days. One open trade per ticker and strategy at a time.

Every strategy is compared with a "random entry" baseline: same stop and
target rules, but entering on every day. If a strategy does not beat the
baseline, the signal adds nothing beyond simply being long the market.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import indicators as ind
from .signals import MIN_DAILY_BARS


@dataclass
class Trade:
    ticker: str
    strategy: str
    signal_date: str
    exit_date: str
    entry: float
    stop: float
    target: float
    exit: float
    r: float
    outcome: str
    days: int


def simulate(df: pd.DataFrame, frame: pd.DataFrame, ticker: str, strategy: str,
             max_hold: int = 20, slippage_bps: float = 5.0) -> list[Trade]:
    slip = slippage_bps / 10_000
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    sig = frame["signal"].to_numpy(bool)
    stops, targets = frame["stop"].to_numpy(float), frame["target"].to_numpy(float)
    dates = [str(pd.Timestamp(x).date()) for x in df.index]
    n = len(df)
    trades: list[Trade] = []
    i = MIN_DAILY_BARS - 1
    while i < n - 1:
        if not sig[i]:
            i += 1
            continue
        entry = o[i + 1] * (1 + slip)
        st, tg = stops[i], targets[i]
        if not (np.isfinite(st) and np.isfinite(tg) and st < entry < tg):
            i += 1  # opened through a level: setup no longer valid
            continue
        last = min(n - 1, i + max_hold)
        exit_px, outcome, j = None, None, i + 1
        while j <= last:
            if l[j] <= st:
                exit_px, outcome = min(st, o[j]), "stop"
                break
            if h[j] >= tg:
                exit_px, outcome = max(tg, o[j]), "hedef"
                break
            j += 1
        if exit_px is None:
            j = last
            exit_px, outcome = c[last], "sure"
        exit_px *= (1 - slip)
        trades.append(Trade(ticker, strategy, dates[i], dates[j], entry, st, tg, exit_px,
                            (exit_px - entry) / (entry - st), outcome, j - i))
        i = j
    return trades


def baseline_frame(df: pd.DataFrame, risk: dict) -> pd.DataFrame:
    """Enter every day, with the same ATR stop and R-multiple target."""
    c = df["Close"]
    a = ind.atr(df, 14)
    stop = c - float(risk.get("atr_carpani", 2.0)) * a
    target = c + float(risk.get("hedef_r", 2.0)) * (c - stop)
    return pd.DataFrame({"signal": a.notna(), "stop": stop, "target": target}, index=df.index)


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t.r for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    streak = best = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    return {
        "n": len(r),
        "win_rate": 100 * len(wins) / len(r),
        "avg_r": float(r.mean()),
        "total_r": float(r.sum()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "max_losing_streak": best,
        "avg_days": float(np.mean([t.days for t in trades])),
    }


def trades_frame(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([asdict(t) for t in trades])
