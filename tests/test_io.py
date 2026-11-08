import io
import json

import numpy as np
import pandas as pd
import pytest

from conftest import breakout_daily, daily_from_closes, et
from tarayici.backtest import baseline_frame, simulate, summarize
from tarayici.config import load_config
from tarayici.data import completed_daily, split_frame
from tarayici.notifier import TelegramNotifier, format_signal, split_message
from tarayici.signals import Signal, breakout_frame


# ------------------------------------------------------------------ data

def _multi(order):
    idx = pd.bdate_range("2026-01-01", periods=3)
    cols = {}
    for t in ["AAA", "BBB"]:
        for f in ["Open", "High", "Low", "Close", "Volume"]:
            cols[(t, f) if order == "ticker" else (f, t)] = [1.0, 2.0, 3.0]
    return pd.DataFrame(cols, index=idx)


@pytest.mark.parametrize("order", ["ticker", "field"])
def test_split_frame_layouts(order):
    out = split_frame(_multi(order), ["AAA", "BBB", "CCC"])
    assert set(out) == {"AAA", "BBB"}
    assert list(out["AAA"].columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_split_frame_flat_single():
    df = _multi("ticker")["AAA"]
    assert set(split_frame(df, ["AAA"])) == {"AAA"}


def test_split_frame_drops_empty_ticker():
    df = _multi("ticker")
    df[("BBB", "Close")] = np.nan
    assert set(split_frame(df, ["AAA", "BBB"])) == {"AAA"}


def test_completed_daily():
    df = daily_from_closes(np.arange(10, 20, dtype=float), end="2026-11-10")
    assert len(completed_daily(df, et(2026, 11, 10, 12, 0))) == 9
    assert len(completed_daily(df, et(2026, 11, 10, 16, 10))) == 10


# -------------------------------------------------------------- telegram

class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_telegram_success_and_payload():
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["data"] = req.data.decode()
        return FakeResp(json.dumps({"ok": True}).encode())

    t = TelegramNotifier("TOKEN123", "42", opener=opener, sleep=lambda s: None)
    assert t.send("<b>merhaba</b>")
    assert "botTOKEN123/sendMessage" in seen["url"]
    assert "chat_id=42" in seen["data"] and "parse_mode=HTML" in seen["data"]


def test_telegram_failure_retries_and_redacts(caplog):
    calls = []

    def opener(req, timeout):
        calls.append(1)
        raise OSError("connection to botTOKEN123 failed")

    t = TelegramNotifier("TOKEN123", "42", retries=2, opener=opener, sleep=lambda s: None)
    assert not t.send("x")
    assert len(calls) == 3
    assert "TOKEN123" not in caplog.text


def test_split_message():
    text = "\n".join(["satır " + "x" * 50] * 200)
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text


def test_format_signal_escapes_html():
    s = Signal("A&B", "kirilim", "daily", pd.Timestamp("2026-11-09"), 100.0, 95.0, 110.0, ("x < y",))
    msg = format_signal(s, et(2026, 11, 9, 16, 15), "Europe/Istanbul")
    assert "A&amp;B" in msg and "x &lt; y" in msg
    assert "16:15 New York · 00:15 Türkiye" in msg


# -------------------------------------------------------------- backtest

def test_simulate_target_stop_and_timeout():
    n = 215
    df = daily_from_closes(np.full(n, 100.0), spread=0.001)
    frame = pd.DataFrame({"signal": False, "stop": 95.0, "target": 110.0}, index=df.index)
    frame.iloc[209, 0] = True          # signal -> enter at bar 210 open
    df.iloc[212, df.columns.get_loc("High")] = 111.0   # target hit on bar 212
    trades = simulate(df, frame, "T", "x", max_hold=20, slippage_bps=0)
    assert len(trades) == 1 and trades[0].outcome == "hedef"
    assert abs(trades[0].r - 2.0) < 1e-9

    df.iloc[211, df.columns.get_loc("Low")] = 94.0     # stop earlier than target
    t2 = simulate(df, frame, "T", "x", slippage_bps=0)[0]
    assert t2.outcome == "stop" and abs(t2.r + 1.0) < 1e-9


def test_backtest_runs_on_real_strategy_frame(cfg):
    df = breakout_daily()
    trades = simulate(df, breakout_frame(df, {}, cfg["risk"]), "T", "kirilim")
    base = simulate(df, baseline_frame(df, cfg["risk"]), "T", "rastgele")
    assert summarize(base)["n"] > 0
    assert isinstance(summarize(trades), dict)


# ---------------------------------------------------------------- config

def test_config_env_override(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("izleme_listesi: [aapl, ' msft ', AAPL]\ntelegram:\n  bot_token: dosyadan\n",
                 encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ortamdan")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = load_config(p)
    assert cfg["izleme_listesi"] == ["AAPL", "MSFT"]
    assert cfg["telegram"]["bot_token"] == "ortamdan"
    assert cfg["risk"]["hedef_r"] == 2.0  # default filled in
