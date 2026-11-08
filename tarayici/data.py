"""Market data. Default source: Yahoo Finance via the free `yfinance` library.

yfinance is unofficial: it can be rate-limited, briefly broken by Yahoo
changes, or a few minutes behind. Every failure is caught and logged so the
scanner simply tries again on the next cycle.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd

from .market_hours import ET, regular_close_dt

log = logging.getLogger("tarayici.data")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]


class DataSource(Protocol):
    def daily(self, tickers: list[str]) -> dict[str, pd.DataFrame]: ...
    def intraday(self, tickers: list[str]) -> dict[str, pd.DataFrame]: ...


def _clean(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty or not set(FIELDS).issubset(df.columns):
        return None
    out = df[FIELDS].copy()
    out = out[out["Close"].notna()]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out["Volume"] = out["Volume"].fillna(0)
    return out if not out.empty else None


def split_frame(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Split a yfinance download into one OHLCV frame per ticker.

    Handles both column layouts yfinance produces: (ticker, field) when
    group_by="ticker", (field, ticker) otherwise, and flat columns for an
    old-style single-ticker download.
    """
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        lvl0 = {str(x) for x in cols.get_level_values(0)}
        field_level = 0 if "Close" in lvl0 else 1
        ticker_level = 1 - field_level
        present = {str(x) for x in cols.get_level_values(ticker_level)}
        for t in tickers:
            if t not in present:
                continue
            df = _clean(raw.xs(t, axis=1, level=ticker_level))
            if df is not None:
                out[t] = df
    elif len(tickers) == 1:
        df = _clean(raw)
        if df is not None:
            out[tickers[0]] = df
    return out


def completed_daily(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Drop today's daily bar until the session has closed (it is still changing)."""
    if df is None or df.empty:
        return df
    today = now.astimezone(ET).date()
    last = pd.Timestamp(df.index[-1])
    last_date = (last.tz_convert(ET) if last.tzinfo else last).date()
    if last_date > today or (last_date == today and now < regular_close_dt(today) + timedelta(minutes=5)):
        return df.iloc[:-1]
    return df


class YFinanceSource:
    def __init__(self, chunk_size: int = 40, pause_sec: float = 1.0,
                 daily_period: str = "2y", interval: str = "5m"):
        self.chunk_size = chunk_size
        self.pause_sec = pause_sec
        self.daily_period = daily_period
        self.interval = interval

    def _download(self, tickers: list[str], **kwargs) -> dict[str, pd.DataFrame]:
        try:
            import yfinance as yf  # imported lazily so tests run without it
        except ImportError:
            log.error("yfinance kurulu değil. Şunu çalıştır: pip install -r requirements.txt")
            return {}

        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(tickers), self.chunk_size):
            chunk = tickers[i:i + self.chunk_size]
            try:
                raw = yf.download(tickers=chunk, group_by="ticker", auto_adjust=True,
                                  progress=False, threads=True, **kwargs)
            except Exception as e:  # network, rate limit, Yahoo changes...
                log.warning("Veri indirilemedi (%d hisse): %s", len(chunk), e)
                continue
            got = split_frame(raw, chunk)
            missing = sorted(set(chunk) - set(got))
            if missing:
                log.info("Veri gelmeyen hisseler: %s", ", ".join(missing))
            out.update(got)
            if i + self.chunk_size < len(tickers):
                time.sleep(self.pause_sec)
        return out

    def daily(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        return self._download(tickers, period=self.daily_period, interval="1d")

    def intraday(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        return self._download(tickers, period="5d", interval=self.interval, prepost=False)
