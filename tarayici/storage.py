"""Persistent state (duplicate-alert guard, open signals) and CSV journals."""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from .signals import Signal

log = logging.getLogger("tarayici.storage")


class State:
    """Small JSON file so a restart does not resend alerts or lose open signals."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = {"alerts": {}, "flags": {}, "open": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                for k in self.data:
                    self.data[k] = loaded.get(k, {})
            except (json.JSONDecodeError, OSError) as e:
                backup = self.path.with_suffix(".bozuk.json")
                log.warning("Durum dosyası okunamadı (%s); %s olarak yedeklendi", e, backup.name)
                os.replace(self.path, backup)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # duplicate guard
    def can_alert(self, key: str, now: datetime, cooldown_hours: float) -> bool:
        last = self.data["alerts"].get(key)
        return last is None or now - datetime.fromisoformat(last) >= timedelta(hours=cooldown_hours)

    def mark_alert(self, key: str, now: datetime) -> None:
        self.data["alerts"][key] = now.isoformat()

    # flags
    def flag(self, name: str):
        return self.data["flags"].get(name)

    def set_flag(self, name: str, value) -> None:
        self.data["flags"][name] = value

    # open signals being tracked until stop / target / timeout
    def add_open(self, sig: Signal, now: datetime) -> str:
        rid = f"{sig.key}:{now.isoformat()}"
        self.data["open"][rid] = {
            "id": rid, "ticker": sig.ticker, "strategy": sig.strategy, "name": sig.name,
            "timeframe": sig.timeframe, "price": sig.price, "stop": sig.stop,
            "target1": sig.target1, "target": sig.target, "risk": sig.price - sig.stop,
            "tp1_hit": False, "tp1_price": None, "checked_until": None,
            "alerted_at": now.isoformat(),
        }
        return rid

    def open_for(self, ticker: str) -> list[dict]:
        return [r for r in self.data["open"].values() if r["ticker"] == ticker]

    def close_open(self, rid: str) -> None:
        self.data["open"].pop(rid, None)

    @property
    def n_open(self) -> int:
        return len(self.data["open"])


class Journal:
    SIGNAL_COLS = ["zaman", "hisse", "strateji", "zaman_dilimi", "fiyat", "stop", "tp1", "tp2", "nedenler"]
    RESULT_COLS = ["sinyal_zamani", "kapanis_zamani", "hisse", "strateji", "sonuc",
                   "sinyal_fiyati", "stop", "tp1", "tp2", "tp1_alindi", "cikis_fiyati", "r"]

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.signals_path = self.folder / "sinyaller.csv"
        self.results_path = self.folder / "sonuclar.csv"

    def _append(self, path: Path, cols: list[str], row: list) -> None:
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(cols)
            w.writerow(row)

    def log_signal(self, sig: Signal, now: datetime) -> None:
        self._append(self.signals_path, self.SIGNAL_COLS, [
            now.isoformat(timespec="seconds"), sig.ticker, sig.strategy, sig.timeframe,
            f"{sig.price:.4f}", f"{sig.stop:.4f}", f"{sig.target1:.4f}", f"{sig.target:.4f}",
            " | ".join(sig.reasons)])

    def log_result(self, rec: dict, outcome: str, exit_price: float, r_mult: float, now: datetime) -> None:
        self._append(self.results_path, self.RESULT_COLS, [
            rec["alerted_at"], now.isoformat(timespec="seconds"), rec["ticker"], rec["strategy"],
            outcome, f"{rec['price']:.4f}", f"{rec['stop']:.4f}", f"{rec.get('target1', 0):.4f}",
            f"{rec['target']:.4f}", "evet" if rec.get("tp1_hit") else "hayir",
            f"{exit_price:.4f}", f"{r_mult:.3f}"])

    def stats(self) -> dict:
        if not self.results_path.exists():
            return {"n": 0}
        with self.results_path.open(newline="", encoding="utf-8-sig") as f:
            rs = [float(row["r"]) for row in csv.DictReader(f) if row.get("r")]
        if not rs:
            return {"n": 0}
        return {"n": len(rs), "win_rate": 100 * sum(r > 0 for r in rs) / len(rs),
                "avg_r": sum(rs) / len(rs), "total_r": sum(rs)}
