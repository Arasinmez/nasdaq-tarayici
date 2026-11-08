"""The 24/7 loop: decides what to scan and when, sends alerts, tracks outcomes."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .data import DataSource, completed_daily
from .market_hours import (ET, close_time, is_trading_day, next_regular_open,
                           previous_trading_day, session, to_et)
from .notifier import format_daily_summary, format_exit, format_open, format_signal
from .signals import Signal, daily_signals, orb_signal
from .storage import Journal, State

log = logging.getLogger("tarayici.scanner")


def _et_index(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    idx = idx.tz_localize(ET) if idx.tz is None else idx.tz_convert(ET)
    return df.set_axis(idx)


def _bar_date(ts) -> date:
    ts = pd.Timestamp(ts)
    return (ts.tz_convert(ET) if ts.tzinfo else ts).date()


class Scanner:
    def __init__(self, cfg: dict, source: DataSource, notifier, state: State, journal: Journal):
        self.cfg = cfg
        self.source = source
        self.notifier = notifier
        self.state = state
        self.journal = journal
        self.tz = cfg["yerel_saat_dilimi"]
        self.tickers: list[str] = cfg["izleme_listesi"]
        z = cfg["zamanlama"]
        self.interval = timedelta(minutes=int(z["gun_ici_aralik_dakika"]))
        self.bar_width = timedelta(minutes=int(z["bar_dakika"]))
        self.bar_delay = timedelta(seconds=int(z["mum_kapanis_gecikmesi_sn"]))
        self.last_intraday: datetime | None = None
        self.daily_retry_after: datetime | None = None

    # ------------------------------------------------------------ alerts

    def _alert(self, sig: Signal, now: datetime) -> bool:
        cooldown = float(self.cfg["takip"]["tekrar_bekleme_saat"])
        if not self.state.can_alert(sig.key, now, cooldown):
            return False
        if any(r["strategy"] == sig.strategy for r in self.state.open_for(sig.ticker)):
            return False  # same setup is still being tracked; don't stack alerts
        if not self.notifier.send(format_signal(sig, now, self.tz)):
            log.warning("%s sinyali gönderilemedi; bir sonraki taramada tekrar denenecek", sig.key)
            return False
        self.journal.log_signal(sig, now)
        self.state.mark_alert(sig.key, now)
        self.state.add_open(sig, now)
        log.info("SİNYAL %s %s @ %.2f", sig.ticker, sig.strategy, sig.price)
        return True

    def _close(self, rec: dict, outcome: str, exit_price: float, now: datetime) -> None:
        risk = rec["price"] - rec["stop"]
        r_mult = (exit_price - rec["price"]) / risk if risk > 0 else 0.0
        self.notifier.send(format_exit(rec, outcome, exit_price, r_mult, now, self.tz))
        self.journal.log_result(rec, outcome, exit_price, r_mult, now)
        self.state.close_open(rec["id"])
        log.info("KAPANDI %s %s %s %.2fR", rec["ticker"], rec["strategy"], outcome, r_mult)

    def _first_hit(self, rec: dict, bars: pd.DataFrame) -> tuple[str, float] | None:
        """Walk bars in time order; if a bar touches both levels assume the stop (conservative)."""
        for _, row in bars.iterrows():
            if row["Low"] <= rec["stop"]:
                return "stop", min(rec["stop"], float(row["Open"]))
            if row["High"] >= rec["target"]:
                return "hedef", max(rec["target"], float(row["Open"]))
        return None

    # ------------------------------------------------------------- scans

    def scan_intraday(self, now: datetime) -> list[Signal]:
        data = self.source.intraday(self.tickers)
        if not data:
            log.warning("Gün içi veri alınamadı")
            return []
        sent = []
        for ticker, bars in data.items():
            b = _et_index(bars)
            done = b[b.index + self.bar_width <= now]
            for rec in self.state.open_for(ticker):
                alerted = datetime.fromisoformat(rec["alerted_at"])
                hit = self._first_hit(rec, done[done.index + self.bar_width > alerted])
                if hit:
                    self._close(rec, hit[0], hit[1], now)
            sig = orb_signal(ticker, b, now, self.cfg)
            if sig and self._alert(sig, now):
                sent.append(sig)
        self.state.save()
        return sent

    def _daily_exits(self, ticker: str, df: pd.DataFrame, now: datetime) -> None:
        max_days = int(self.cfg["takip"]["gunluk_sinyal_max_gun"])
        dates = [_bar_date(ts) for ts in df.index]
        for rec in self.state.open_for(ticker):
            a_date = datetime.fromisoformat(rec["alerted_at"]).astimezone(ET).date()
            if rec["timeframe"] == "intraday":
                idx = [i for i, d in enumerate(dates) if d >= a_date]
                if idx:  # the alert day's session is over: close at its closing price
                    self._close(rec, "gun_sonu", float(df["Close"].iloc[idx[0]]), now)
                continue
            later = df.iloc[[i for i, d in enumerate(dates) if d > a_date]]
            hit = self._first_hit(rec, later)
            if hit:
                self._close(rec, hit[0], hit[1], now)
            elif len(later) >= max_days:
                self._close(rec, "sure_doldu", float(df["Close"].iloc[-1]), now)

    def scan_daily(self, now: datetime, day: date) -> list[Signal] | None:
        """Scan completed daily bars. Returns None if no data came back (retry later)."""
        data = self.source.daily(self.tickers)
        if not data:
            log.warning("Günlük veri alınamadı; 10 dakika sonra tekrar denenecek")
            return None
        sent, scanned = [], 0
        for ticker, raw in data.items():
            df = completed_daily(raw, now)
            if df is None or df.empty:
                continue
            scanned += 1
            self._daily_exits(ticker, df, now)
            for sig in daily_signals(ticker, df, self.cfg):
                if self._alert(sig, now):
                    sent.append(sig)
        self.notifier.send(format_daily_summary(
            day, scanned, len(self.tickers), sent, self.state.n_open,
            self.journal.stats(), next_regular_open(now), self.tz))
        self.state.save()
        return sent

    # --------------------------------------------------------- scheduling

    def daily_scan_dt(self, d: date) -> datetime:
        delay = timedelta(minutes=int(self.cfg["zamanlama"]["gunluk_tarama_gecikme_dk"]))
        return datetime.combine(d, close_time(d), tzinfo=ET) + delay

    def daily_target(self, now: datetime) -> date:
        """Most recent trading day whose daily scan time has passed."""
        d = to_et(now).date()
        if is_trading_day(d) and now >= self.daily_scan_dt(d):
            return d
        return previous_trading_day(d)

    def tick(self, now: datetime) -> float:
        """Do whatever is due at `now`; return seconds to sleep until the next check."""
        d = to_et(now).date()
        if session(now) == "regular":
            if self.cfg["zamanlama"]["acilis_bildirimi"] and self.state.flag("open_notified") != d.isoformat():
                self.notifier.send(format_open(len(self.tickers), now, self.tz))
                self.state.set_flag("open_notified", d.isoformat())
                self.state.save()
            if self.last_intraday is None or now - self.last_intraday >= self.interval - timedelta(seconds=30):
                self.last_intraday = now
                self.scan_intraday(now)
        target = self.daily_target(now)
        if self.state.flag("daily_done") != target.isoformat() and (
                self.daily_retry_after is None or now >= self.daily_retry_after):
            if self.scan_daily(now, target) is None:
                self.daily_retry_after = now + timedelta(minutes=10)
            else:
                self.daily_retry_after = None
                self.state.set_flag("daily_done", target.isoformat())
                self.state.save()
        return self.sleep_seconds(now)

    def sleep_seconds(self, now: datetime) -> float:
        n = to_et(now)
        if session(now) == "regular":
            step = int(self.interval.total_seconds() // 60)
            floor = n.replace(minute=(n.minute // step) * step, second=0, microsecond=0)
            nxt = floor + self.bar_delay
            while nxt <= n:
                nxt += self.interval
            secs = (nxt - n).total_seconds()
        else:
            cands = [next_regular_open(now) + self.bar_delay]
            if is_trading_day(n.date()) and self.daily_scan_dt(n.date()) > now:
                cands.append(self.daily_scan_dt(n.date()))
            if self.daily_retry_after and self.daily_retry_after > now:
                cands.append(self.daily_retry_after)
            secs = min((c - now).total_seconds() for c in cands)
        return max(5.0, min(secs, 900.0))

    def run_forever(self, clock=lambda: datetime.now(timezone.utc), sleep=time.sleep,
                    max_ticks: int | None = None) -> None:
        log.info("Bot başladı: %d hisse izleniyor", len(self.tickers))
        last_beat = None
        i = 0
        while max_ticks is None or i < max_ticks:
            now = clock()
            try:
                secs = self.tick(now)
            except Exception:
                log.exception("Beklenmeyen hata; 60 saniye sonra tekrar denenecek")
                secs = 60.0
            if last_beat is None or now - last_beat >= timedelta(hours=1):
                log.info("Çalışıyor — seans: %s, takipteki sinyal: %d, sonraki kontrol %.0f sn sonra",
                         session(now), self.state.n_open, secs)
                last_beat = now
            sleep(secs)
            i += 1
