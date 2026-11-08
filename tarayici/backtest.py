"""Historical test of the daily strategies, using the same code as live.

Rules: enter at the next day's open (plus slippage). Levels come from the
signal (stop, TP1, TP2). If the open is already below the stop or above TP1,
the setup is skipped. Then the live plan runs bar by bar: half out at TP1 and
stop to entry, the rest at TP2 / break-even / timeout (`max_hold` days).
One open trade per ticker and strategy at a time.

Each strategy is compared with a "random entry" baseline that uses the same
exits and the same filters but enters on every allowed day. If a strategy
does not clearly beat that baseline, the signal itself adds nothing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import indicators as ind
from .bracket import total_r, walk_bracket
from .signals import MIN_DAILY_BARS


@dataclass
class Trade:
    ticker: str
    strategy: str
    signal_date: str
    exit_date: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp1_hit: bool
    exit: float
    r: float
    outcome: str
    days: int


def simulate(df: pd.DataFrame, frame: pd.DataFrame, ticker: str, strategy: str,
             max_hold: int = 20, slippage_bps: float = 5.0, risk_cfg: dict | None = None,
             mask: pd.Series | None = None) -> list[Trade]:
    risk_cfg = risk_cfg or {}
    h1 = float(risk_cfg.get("hedef1_r", 1.0))
    slip = slippage_bps / 10_000
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    sig = frame["signal"].to_numpy(bool)
    if mask is not None:
        sig = sig & mask.reindex(df.index).fillna(False).to_numpy(bool)
    stops, tp2s = frame["stop"].to_numpy(float), frame["target"].to_numpy(float)
    dates = [str(pd.Timestamp(x).date()) for x in df.index]
    n = len(df)
    trades: list[Trade] = []
    i = MIN_DAILY_BARS - 1
    while i < n - 1:
        if not sig[i]:
            i += 1
            continue
        st, tp2 = stops[i], tp2s[i]
        tp1 = c[i] + h1 * (c[i] - st)
        entry = o[i + 1] * (1 + slip)
        if not (np.isfinite(st) and np.isfinite(tp2) and st < entry < tp1 <= tp2):
            i += 1  # opened through a level: setup no longer valid
            continue
        last = min(n - 1, i + max_hold)
        tp1_hit, tp1_px, final, outcome = False, None, None, None
        j = i + 1
        while j <= last:
            events, tp1_hit, closed = walk_bracket(entry, st, tp1, tp2, [(o[j], h[j], l[j])], tp1_hit)
            for kind, px in events:
                if kind == "tp1":
                    tp1_px = px * (1 - slip)
                else:
                    final, outcome = px, kind
            if closed:
                break
            j += 1
        if final is None:
            j, final, outcome = last, c[last], "sure"
        final *= (1 - slip)
        r = total_r(entry, entry - st, tp1_px, final)
        trades.append(Trade(ticker, strategy, dates[i], dates[j], entry, st, tp1, tp2,
                            tp1_hit, final, r, outcome, j - i))
        i = j
    return trades


def baseline_frame(df: pd.DataFrame, risk: dict) -> pd.DataFrame:
    """Enter every day, with the same ATR stop and R-multiple targets."""
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
        "tp1_rate": 100 * np.mean([t.tp1_hit for t in trades]),
        "avg_r": float(r.mean()),
        "margin": float(2 * r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else float("nan"),
        "total_r": float(r.sum()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "max_losing_streak": best,
        "avg_days": float(np.mean([t.days for t in trades])),
    }


def trades_frame(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([asdict(t) for t in trades])
