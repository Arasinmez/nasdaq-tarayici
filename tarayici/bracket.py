"""Trade management shared by the live tracker and the backtest.

Plan for every signal:
  * stop below entry, TP1 = 1R, TP2 = 2R (configurable)
  * at TP1 sell half and move the stop to the entry price (break-even)
  * the other half exits at TP2, at the break-even stop, or on timeout

Bars are processed in time order. If one bar touches both the stop and a
target, the stop is assumed to have come first (conservative).
"""
from __future__ import annotations

from typing import Iterable


def walk_bracket(entry: float, stop: float, tp1: float, tp2: float,
                 bars: Iterable[tuple[float, float, float]], tp1_hit: bool = False):
    """bars: (open, high, low) tuples AFTER the entry.

    Returns (events, tp1_hit, closed). events is a list of (kind, price) with
    kind in {"tp1", "tp2", "stop", "be"} ("be" = break-even stop after TP1).
    """
    events: list[tuple[str, float]] = []
    for o, h, l in bars:
        if not tp1_hit:
            if l <= stop:
                events.append(("stop", min(stop, o)))
                return events, False, True
            if h >= tp1:
                events.append(("tp1", max(tp1, o)))
                tp1_hit = True
                if h >= tp2:
                    events.append(("tp2", max(tp2, o)))
                    return events, True, True
            continue
        if l <= entry:
            events.append(("be", min(entry, o)))
            return events, True, True
        if h >= tp2:
            events.append(("tp2", max(tp2, o)))
            return events, True, True
    return events, tp1_hit, False


def total_r(entry: float, risk: float, tp1_price: float | None, final_price: float) -> float:
    """R multiple of the whole trade: half at TP1 (if reached), the rest at final_price."""
    if risk <= 0:
        return 0.0
    if tp1_price is None:
        return (final_price - entry) / risk
    return 0.5 * (tp1_price - entry) / risk + 0.5 * (final_price - entry) / risk
