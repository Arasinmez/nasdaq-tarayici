"""The 24/7 loop: decides what to scan and when, sends alerts, tracks outcomes."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .bracket import total_r, walk_bracket
from .data import DataSource, completed_daily
from .market_hours import (ET, close_time, is_trading_day, next_regular_open,
                           previous_trading_day, session, to_et)
from .notifier import (format_daily_summary, format_exit, format_open, format_signal,
                       format_tp1)
from .signals import Signal, daily_signals, filters_active, market_ok_now, orb_signal
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
        self.index_ticker = str(cfg.get("filtreler", {}).get("endeks", "QQQ")).upper()
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
        r_mult = total_r(rec["price"], rec.get("risk", rec["price"] - rec["stop"]),
                         rec.get("tp1_price"), exit_price)
        self.notifier.send(format_exit(rec, outcome, exit_price, r_mult, now, self.tz))
        self.journal.log_result(rec, outcome, exit_price, r_mult, now)
        self.state.close_open(rec["id"])
        log.info("KAPANDI %s %s %s %.2fR", rec["ticker"], rec["strategy"], outcome, r_mult)

    def _apply(self, rec: dict, bars: pd.DataFrame, now: datetime) -> bool:
        """Run the TP1/TP2 plan over new bars. Returns True if the trade closed."""
        events, _, closed = walk_bracket(
            rec["price"], rec["stop"], rec.get("target1", rec["target"]), rec["target"],
            zip(bars["Open"], bars["High"], bars["Low"]), bool(rec.get("tp1_hit")))
        for kind, px in events:
            if kind == "tp1":
                rec["tp1_hit"], rec["tp1_price"] = True, float(px)
                self.notifier.send(format_tp1(rec, float(px), now, self.tz))
                log.info("TP1 %s %s @ %.2f", rec["ticker"], rec["strategy"], px)
            else:
                self._close(rec, kind, float(px), now)
        return closed

    def _track_intraday(self, ticker: str, done: pd.DataFrame, now: datetime) -> None:
        for rec in self.state.open_for(ticker):
            start = datetime.fromisoformat(rec.get("checked_until") or rec["alerted_at"])
            new = done[done.index + self.bar_width > start]
            if new.empty:
                continue
            if not self._apply(rec, new, now):
                rec["checked_until"] = (new.index[-1] + self.bar_width).isoformat()

    def _track_open_intraday(self, now: datetime) -> None:
        tickers = sorted({r["ticker"] for r in self.state.data["open"].values()})
        if not tickers:
            return
        for ticker, bars in self.source.intraday(tickers).items():
            b = _et_index(bars)
            self._track_intraday(ticker, b[b.index + self.bar_width <= now], now)

    def _daily_exits(self, ticker: str, df: pd.DataFrame, now: datetime) -> None:
        max_days = int(self.cfg["takip"]["gunluk_sinyal_max_gun"])
        dates = [_bar_date(ts) for ts in df.index]
        for rec in self.state.open_for(ticker):
            a_date = datetime.fromisoformat(rec["alerted_at"]).astimezone(ET).date()
            if rec["timeframe"] == "intraday":
                idx = [i for i, d in enumerate(dates) if d >= a_date]
                if idx:  # the alert day's session is over: close the rest at that close
                    self._close(rec, "gun_sonu", float(df["Close"].iloc[idx[0]]), now)
                continue
            c = rec.get("checked_until")
            from_date = max(a_date, datetime.fromisoformat(c).astimezone(ET).date()) if c else a_date
            later = df.iloc[[i for i, d in enumerate(dates) if d > from_date]]
            if not later.empty:
                if self._apply(rec, later, now):
                    continue
                last = _bar_date(later.index[-1])
                rec["checked_until"] = datetime.combine(last, close_time(last), tzinfo=ET).isoformat()
            if sum(d > a_date for d in dates) >= max_days:
                self._close(rec, "sure_doldu", float(df["Close"].iloc[-1]), now)

    # ------------------------------------------------------------- scans

    def scan_intraday(self, now: datetime) -> list[Signal]:
        data = self.source.intraday(self.tickers)
        if not data:
            log.warning("Gün içi veri alınamadı")
            return []
        market_ok = self.state.flag("piyasa_ok")
        sent = []
        for ticker, bars in data.items():
            b = _et_index(bars)
            self._track_intraday(ticker, b[b.index + self.bar_width <= now], now)
            sig = orb_signal(ticker, b, now, self.cfg, market_ok)
            if sig and self._alert(sig, now):
                sent.append(sig)
        self.state.save()
        return sent

    def scan_daily(self, now: datetime, day: date) -> list[Signal] | None:
        """Scan completed daily bars. Returns None if no data came back (retry later)."""
        use_filters = filters_active(self.cfg)
        wanted = list(self.tickers)
        if use_filters and self.index_ticker not in wanted:
            wanted.append(self.index_ticker)
        data = self.source.daily(wanted)
        if not set(data) & set(self.tickers):
            log.warning("Günlük veri alınamadı; 10 dakika sonra tekrar denenecek")
            return None
        mkt = completed_daily(data[self.index_ticker], now) if self.index_ticker in data else None
        market_ok = market_ok_now(mkt, self.cfg) if use_filters else None
        self.state.set_flag("piyasa_ok", market_ok)
        self._track_open_intraday(now)
        sent, scanned = [], 0
        for ticker in self.tickers:
            df = completed_daily(data.get(ticker), now)
            if df is None or df.empty:
                continue
            scanned += 1
            self._daily_exits(ticker, df, now)
            for sig in daily_signals(ticker, df, self.cfg, mkt):
                if self._alert(sig, now):
                    sent.append(sig)
        self.notifier.send(format_daily_summary(
            day, scanned, len(self.tickers), sent, self.state.n_open,
            self.journal.stats(), next_regular_open(now), self.tz,
            market_ok if use_filters else "kapali"))
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
        # daily scan first, so the market filter is fresh before any intraday scan
        target = self.daily_target(now)
        if self.state.flag("daily_done") != target.isoformat() and (
                self.daily_retry_after is None or now >= self.daily_retry_after):
            if self.scan_daily(now, target) is None:
                self.daily_retry_after = now + timedelta(minutes=10)
            else:
                self.daily_retry_after = None
                self.state.set_flag("daily_done", target.isoformat())
                self.state.save()
        if session(now) == "regular":
            if self.cfg["zamanlama"]["acilis_bildirimi"] and self.state.flag("open_notified") != d.isoformat():
                self.notifier.send(format_open(len(self.tickers), now, self.tz,
                                              int(self.interval.total_seconds() // 60)))
                self.state.set_flag("open_notified", d.isoformat())
                self.state.save()
            if self.last_intraday is None or now - self.last_intraday >= self.interval - timedelta(seconds=30):
                self.last_intraday = now
                self.scan_intraday(now)
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
