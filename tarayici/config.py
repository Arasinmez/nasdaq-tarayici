"""Load config.yaml and fill in defaults for anything missing."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "izleme_listesi": ["AAPL", "MSFT", "NVDA"],
    "yerel_saat_dilimi": "Europe/Istanbul",
    "veri_klasoru": "veri",
    "telegram": {"aktif": True, "bot_token": "", "chat_id": ""},
    "zamanlama": {
        "gun_ici_aralik_dakika": 5,
        "bar_dakika": 5,
        "mum_kapanis_gecikmesi_sn": 20,
        "gunluk_tarama_gecikme_dk": 15,
        "acilis_bildirimi": True,
    },
    "stratejiler": {
        "kirilim": {"aktif": True, "zirve_gun": 20, "hacim_kati": 1.5},
        "trend_geri_cekilme": {"aktif": True, "rsi_esik": 40},
        "acilis_araligi_kirilimi": {
            "aktif": True, "aralik_dakika": 15, "hacim_kati": 1.5, "son_saat_et": "11:30",
            "taze_dakika": 15,
        },
    },
    "risk": {"atr_carpani": 2.0, "hedef_r": 2.0},
    "takip": {"tekrar_bekleme_saat": 20, "gunluk_sinyal_max_gun": 20},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path = "config.yaml") -> dict:
    path = Path(path)
    user = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, user)
    # Secrets are better kept in environment variables than in the file.
    tg = cfg["telegram"]
    # strip(): pasted secrets often carry a trailing newline or spaces
    tg["bot_token"] = (os.environ.get("TELEGRAM_BOT_TOKEN") or str(tg.get("bot_token") or "")).strip()
    tg["chat_id"] = (os.environ.get("TELEGRAM_CHAT_ID") or str(tg.get("chat_id") or "")).strip()
    cfg["izleme_listesi"] = list(dict.fromkeys(
        str(t).strip().upper() for t in cfg["izleme_listesi"] if str(t).strip()
    ))
    return cfg
