"""Notifications: console and Telegram (standard library only)."""
from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from .market_hours import ET
from .signals import Signal

log = logging.getLogger("tarayici.notifier")
TELEGRAM_API = "https://api.telegram.org"
MAX_LEN = 4000  # Telegram limit is 4096 characters


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"</?[a-z]+>", "", text))


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # pathological single long line
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


class ConsoleNotifier:
    def send(self, text: str) -> bool:
        print(strip_tags(text), flush=True)
        print("-" * 40, flush=True)
        return True


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: float = 10, retries: int = 2,
                 opener=urllib.request.urlopen, sleep=time.sleep):
        if not token or not chat_id:
            raise ValueError("Telegram bot_token ve chat_id gerekli")
        self.token, self.chat_id = token, str(chat_id)
        self.timeout, self.retries = timeout, retries
        self._open, self._sleep = opener, sleep

    def _redact(self, msg) -> str:
        return str(msg).replace(self.token, "***")

    def _send_one(self, text: str) -> bool:
        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, data=data, method="POST")
                with self._open(req, timeout=self.timeout) as r:
                    body = json.loads(r.read().decode("utf-8"))
                if body.get("ok"):
                    return True
                log.error("Telegram mesajı reddetti: %s", self._redact(body.get("description")))
                return False
            except Exception as e:
                log.warning("Telegram hatası (deneme %d): %s", attempt + 1, self._redact(e))
                if attempt < self.retries:
                    self._sleep(2 * (attempt + 1))
        return False

    def send(self, text: str) -> bool:
        return all([self._send_one(p) for p in split_message(text)])


class MultiNotifier:
    def __init__(self, *notifiers):
        self.notifiers = [n for n in notifiers if n is not None]

    def send(self, text: str) -> bool:
        results = [n.send(text) for n in self.notifiers]
        return all(results)  # if Telegram fails, the alert is retried on the next scan


def find_chat_ids(token: str, opener=urllib.request.urlopen) -> list[tuple[str, str]]:
    """Chats that have recently messaged the bot: [(chat_id, name), ...]."""
    with opener(f"{TELEGRAM_API}/bot{token}/getUpdates", timeout=10) as r:
        body = json.loads(r.read().decode("utf-8"))
    seen: dict[str, str] = {}
    for upd in body.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            name = chat.get("title") or " ".join(
                x for x in [chat.get("first_name"), chat.get("last_name")] if x) or chat.get("username", "")
            seen[str(chat["id"])] = name
    return list(seen.items())


# ------------------------------------------------------------ formatting

def fmt_time(now: datetime, local_tz: str) -> str:
    et = now.astimezone(ET).strftime("%H:%M")
    loc = now.astimezone(ZoneInfo(local_tz)).strftime("%H:%M")
    return f"{et} New York · {loc} Türkiye"


DISCLAIMER = "<i>Otomatik teknik sinyal, yatırım tavsiyesi değil. Kararı ve riski sen yönetirsin.</i>"


def format_signal(sig: Signal, now: datetime, local_tz: str) -> str:
    when = ("Günlük kapanışta oluştu; bir sonraki seansın açılışında fiyatı kontrol et."
            if sig.timeframe == "daily" else "Az önce kapanan 5 dakikalık mumda oluştu.")
    lines = [
        f"🟢 <b>GİRİŞ SİNYALİ — {esc(sig.ticker)}</b>",
        f"Strateji: {esc(sig.name)}",
        "",
        f"Giriş: <b>{sig.price:.2f} $</b>",
        f"Stop: {sig.stop:.2f} $ (−{sig.risk_pct:.1f}%)",
        f"TP1: {sig.target1:.2f} $ (+{sig.tp1_pct:.1f}%)",
        f"TP2: {sig.target:.2f} $ (+{sig.reward_pct:.1f}%)",
        "<i>Plan: TP1'de yarısını sat, stop'u giriş fiyatına çek. Kalanı TP2'de sat.</i>",
        "",
        "<b>Neden:</b>",
        *[f"• {esc(r)}" for r in sig.reasons],
        "",
        esc(when),
        f"🕒 {fmt_time(now, local_tz)}",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def format_tp1(rec: dict, price: float, now: datetime, local_tz: str) -> str:
    chg = (price - rec["price"]) / rec["price"] * 100
    return "\n".join([
        f"🎯 <b>TP1 GELDİ — {esc(rec['ticker'])}</b>",
        f"{rec['price']:.2f} $ → {price:.2f} $ ({chg:+.1f}%)",
        f"Plana göre: yarısını sat, stop'u giriş fiyatına (<b>{rec['price']:.2f} $</b>) çek.",
        f"Kalan yarı için TP2: {rec['target']:.2f} $",
        f"🕒 {fmt_time(now, local_tz)}",
    ])


OUTCOME_LABELS = {
    "tp2": ("🎯🎯", "TP2 GELDİ — İŞLEM TAMAM"),
    "stop": ("🔴", "STOP OLDU"),
    "be": ("⚪", "STOP GİRİŞTE — KALAN YARI BAŞA BAŞ KAPANDI"),
    "gun_sonu": ("⏹", "GÜN SONU KAPANDI"),
    "sure_doldu": ("⏹", "TAKİP SÜRESİ DOLDU"),
}


def format_exit(rec: dict, outcome: str, exit_price: float, r_mult: float,
                now: datetime, local_tz: str) -> str:
    icon, label = OUTCOME_LABELS.get(outcome, ("⏹", outcome.upper()))
    chg = (exit_price - rec["price"]) / rec["price"] * 100
    lines = [
        f"{icon} <b>{label} — {esc(rec['ticker'])}</b>",
        f"Strateji: {esc(rec.get('name', rec['strategy']))}",
        f"Giriş {rec['price']:.2f} $ → çıkış {exit_price:.2f} $ ({chg:+.1f}%)",
    ]
    if rec.get("tp1_hit"):
        lines.append(f"TP1'de yarısı {rec['tp1_price']:.2f} $'dan satılmıştı.")
    lines += [f"İşlemin toplam sonucu: <b>{r_mult:+.2f}R</b>", f"🕒 {fmt_time(now, local_tz)}"]
    return "\n".join(lines)


def format_open(n_tickers: int, now: datetime, local_tz: str, minutes: int = 10) -> str:
    return (f"🔔 <b>Piyasa açıldı.</b> {n_tickers} hisse {minutes} dakikada bir taranıyor.\n"
            f"🕒 {fmt_time(now, local_tz)}")


MARKET_LINES = {
    True: "Piyasa: ✅ Nasdaq yükseliş trendinde (200 günlük ortalamanın üstünde)",
    False: "Piyasa: ⛔ Nasdaq 200 günlük ortalamanın altında. Yeni sinyal verilmiyor.",
    None: "Piyasa: ❔ endeks verisi alınamadı, filtreli sinyaller bekletiliyor",
}


def format_daily_summary(day, n_scanned: int, n_requested: int, signals: list[Signal],
                         n_open: int, stats: dict, next_open: datetime, local_tz: str,
                         market_ok: bool | None | str = "kapali") -> str:
    lines = [f"📊 <b>Günlük tarama — {day:%d.%m.%Y}</b>",
             f"{n_scanned}/{n_requested} hisse tarandı, {len(signals)} yeni sinyal."]
    if market_ok != "kapali":
        lines.append(MARKET_LINES.get(market_ok, MARKET_LINES[None]))
    for s in signals:
        lines.append(f"• {esc(s.ticker)}: {esc(s.name)} @ {s.price:.2f} $")
    lines.append(f"Takipte açık sinyal: {n_open}")
    if stats.get("n", 0):
        lines.append(f"Geçmiş: {stats['n']} kapanan sinyal, isabet %{stats['win_rate']:.0f}, "
                     f"ortalama {stats['avg_r']:+.2f}R")
    no = next_open.astimezone(ZoneInfo(local_tz))
    lines.append(f"Sonraki açılış: {no:%d.%m %H:%M} (Türkiye)")
    return "\n".join(lines)
