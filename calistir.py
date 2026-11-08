"""Nasdaq tarayıcıyı başlatır.

Kullanım:
  python calistir.py                 7/24 çalışır (Ctrl+C ile durur)
  python calistir.py --simdi         Hemen bir tarama yapar ve çıkar
  python calistir.py --tek-sefer     Zamanı gelen işi bir kez yapar (GitHub Actions bunu kullanır)
  python calistir.py --kuru          Telegram'a göndermeden, sadece ekrana yazar
  python calistir.py --telegram-test Telegram'a deneme mesajı yollar
  python calistir.py --chat-id-bul   Telegram chat id'ni bulur
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tarayici.config import load_config  # noqa: E402
from tarayici.data import YFinanceSource  # noqa: E402
from tarayici.market_hours import session  # noqa: E402
from tarayici.notifier import (ConsoleNotifier, MultiNotifier, TelegramNotifier,  # noqa: E402
                               find_chat_ids)
from tarayici.scanner import Scanner  # noqa: E402
from tarayici.storage import Journal, State  # noqa: E402

log = logging.getLogger("tarayici")


def setup_logging(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_h = RotatingFileHandler(folder / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_h.setFormatter(fmt)
    console_h = logging.StreamHandler()
    console_h.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_h)
    root.addHandler(console_h)
    for noisy in ("yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_scanner(cfg: dict, folder: Path, dry_run: bool) -> Scanner:
    notifiers = [ConsoleNotifier()]
    tg = cfg["telegram"]
    if tg["aktif"] and not dry_run:
        if tg["bot_token"] and tg["chat_id"]:
            notifiers.append(TelegramNotifier(tg["bot_token"], tg["chat_id"]))
        else:
            log.warning("Telegram ayarlı değil (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). "
                        "Sinyaller sadece ekrana yazılacak.")
    return Scanner(cfg, YFinanceSource(), MultiNotifier(*notifiers),
                   State(folder / "durum.json"), Journal(folder))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows konsolunda Türkçe karakter / emoji
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Nasdaq hisse tarayıcı (sinyal botu, emir göndermez)")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--kuru", action="store_true", help="Telegram'a gönderme, sadece ekrana yaz")
    ap.add_argument("--simdi", action="store_true", help="Hemen bir tarama yap ve çık")
    ap.add_argument("--tek-sefer", action="store_true",
                    help="Zamanı gelen işi bir kez yap ve çık (GitHub Actions / cron için)")
    ap.add_argument("--telegram-test", action="store_true", help="Telegram'a deneme mesajı gönder")
    ap.add_argument("--chat-id-bul", action="store_true", help="Bota yazan sohbetlerin chat id'lerini göster")
    args = ap.parse_args()

    cfg = load_config(args.config)
    folder = HERE / cfg["veri_klasoru"]
    setup_logging(folder)
    tg = cfg["telegram"]

    if args.chat_id_bul:
        if not tg["bot_token"]:
            print("Önce TELEGRAM_BOT_TOKEN ayarla (README'de anlatılıyor).")
            return 1
        try:
            chats = find_chat_ids(tg["bot_token"])
        except Exception as e:
            print(f"Telegram'a bağlanılamadı ({str(e).replace(tg['bot_token'], '***')}). "
                  "Token doğru mu, internet var mı kontrol et.")
            return 1
        if not chats:
            print("Hiç mesaj bulunamadı. Telegram'da botuna bir mesaj yaz, sonra tekrar çalıştır.")
            return 1
        for cid, name in chats:
            print(f"chat_id: {cid}   ({name})")
        return 0

    if args.telegram_test:
        if not (tg["bot_token"] and tg["chat_id"]):
            print("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID ayarlı değil.")
            return 1
        ok = TelegramNotifier(tg["bot_token"], tg["chat_id"]).send(
            "✅ <b>Nasdaq tarayıcı bağlandı.</b> Sinyaller buraya gelecek.")
        print("Gönderildi, Telegram'ı kontrol et." if ok else "Gönderilemedi, bot.log dosyasına bak.")
        return 0 if ok else 1

    scanner = build_scanner(cfg, folder, args.kuru)

    if args.tek_sefer:
        now = datetime.now(timezone.utc)
        log.info("Tek tur — seans: %s", session(now))
        scanner.tick(now)
        return 0

    if args.simdi:
        now = datetime.now(timezone.utc)
        day = scanner.daily_target(now)
        log.info("Günlük tarama (%s kapanışı)...", day)
        scanner.scan_daily(now, day)
        if session(now) == "regular":
            log.info("Gün içi tarama...")
            scanner.scan_intraday(now)
        return 0

    try:
        scanner.run_forever()
    except KeyboardInterrupt:
        log.info("Durduruldu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
