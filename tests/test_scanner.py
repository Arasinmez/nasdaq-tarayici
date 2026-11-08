import csv
from datetime import timedelta

from conftest import FakeNotifier, FakeSource, breakout_daily, et, intraday_day, orb_bars
from tarayici.scanner import Scanner
from tarayici.storage import Journal, State


def make(cfg, tmp_path, source, ok=True):
    n = FakeNotifier(ok)
    s = Scanner(cfg, source, n, State(tmp_path / "durum.json"), Journal(tmp_path))
    return s, n


def test_daily_scan_alerts_once(cfg, tmp_path):
    src = FakeSource(daily={"TEST": breakout_daily()})
    s, n = make(cfg, tmp_path, src)
    now = et(2026, 11, 10, 8, 0)  # Tuesday pre-market -> scans Monday's close
    sent = s.scan_daily(now, s.daily_target(now))
    assert [x.strategy for x in sent] == ["kirilim"]
    assert any("GİRİŞ SİNYALİ — TEST" in m for m in n.messages)
    assert any("Günlük tarama" in m for m in n.messages)
    # second scan: no duplicate alert
    assert s.scan_daily(now + timedelta(minutes=30), s.daily_target(now)) == []
    rows = list(csv.DictReader((tmp_path / "sinyaller.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 1 and rows[0]["hisse"] == "TEST"


def test_failed_send_is_retried(cfg, tmp_path):
    src = FakeSource(daily={"TEST": breakout_daily()})
    s, n = make(cfg, tmp_path, src, ok=False)
    now = et(2026, 11, 10, 8, 0)
    assert s.scan_daily(now, s.daily_target(now)) == []
    n.ok = True
    assert len(s.scan_daily(now, s.daily_target(now))) == 1


def test_todays_partial_daily_bar_is_ignored(cfg, tmp_path):
    # breakout bar is "today" and the market is still open -> must not alert
    src = FakeSource(daily={"TEST": breakout_daily(end="2026-11-10")})
    s, _ = make(cfg, tmp_path, src)
    now = et(2026, 11, 10, 11, 0)
    assert s.scan_daily(now, s.daily_target(now)) == []


def test_tick_during_session(cfg, tmp_path):
    src = FakeSource(daily={"TEST": breakout_daily()},
                     intraday={"TEST": intraday_day(bars=orb_bars())})
    s, n = make(cfg, tmp_path, src)
    sleep = s.tick(et(2026, 11, 10, 10, 0, 20))
    text = "\n".join(n.messages)
    assert "Piyasa açıldı" in text
    assert "Açılış aralığı kırılımı" in text       # intraday ORB alert
    assert "Kırılım (günlük)" in text               # Monday's daily scan caught up
    assert src.calls == {"daily": 1, "intraday": 1}
    assert 0 < sleep <= 300
    # a few seconds later nothing is re-run or re-sent
    before = len(n.messages)
    s.tick(et(2026, 11, 10, 10, 0, 40))
    assert len(n.messages) == before
    assert src.calls == {"daily": 1, "intraday": 1}


def test_target_hit_is_tracked(cfg, tmp_path):
    bars = orb_bars()
    src = FakeSource(intraday={"TEST": intraday_day(bars=bars)})
    s, n = make(cfg, tmp_path, src)
    s.state.set_flag("daily_done", "2026-11-09")
    s.state.set_flag("open_notified", "2026-11-10")
    s.tick(et(2026, 11, 10, 10, 0, 20))
    assert s.state.n_open == 1
    # next bar runs through the target (101.6 + 2*1.1 = 103.8)
    src.intraday_data["TEST"] = intraday_day(bars=bars + [(101.7, 104.2, 101.5, 104.0, 90_000)])
    s.tick(et(2026, 11, 10, 10, 5, 20))
    assert s.state.n_open == 0
    assert any("HEDEFE ULAŞTI" in m for m in n.messages)
    stats = s.journal.stats()
    assert stats["n"] == 1 and abs(stats["avg_r"] - 2.0) < 1e-6


def test_stop_wins_when_bar_hits_both(cfg, tmp_path):
    bars = orb_bars()
    src = FakeSource(intraday={"TEST": intraday_day(bars=bars)})
    s, n = make(cfg, tmp_path, src)
    s.state.set_flag("daily_done", "2026-11-09")
    s.tick(et(2026, 11, 10, 10, 0, 20))
    src.intraday_data["TEST"] = intraday_day(bars=bars + [(101.6, 104.5, 100.0, 102.0, 90_000)])
    s.tick(et(2026, 11, 10, 10, 5, 20))
    assert any("STOP OLDU" in m for m in n.messages)
    assert abs(s.journal.stats()["avg_r"] + 1.0) < 1e-6


def test_intraday_signal_closed_at_day_end(cfg, tmp_path):
    src = FakeSource(intraday={"TEST": intraday_day(bars=orb_bars())})
    s, n = make(cfg, tmp_path, src)
    s.state.set_flag("daily_done", "2026-11-09")
    s.tick(et(2026, 11, 10, 10, 0, 20))
    assert s.state.n_open == 1
    daily = breakout_daily(end="2026-11-10")
    src.daily_data["TEST"] = daily
    s.tick(et(2026, 11, 10, 16, 16))
    assert s.state.n_open == 0 or all(r["timeframe"] == "daily" for r in s.state.data["open"].values())
    assert any("GÜN SONU" in m for m in n.messages)


def test_sleep_seconds(cfg, tmp_path):
    s, _ = make(cfg, tmp_path, FakeSource())
    assert s.sleep_seconds(et(2026, 11, 10, 10, 0, 10)) == 10
    assert s.sleep_seconds(et(2026, 11, 10, 10, 3, 0)) == 140
    assert s.sleep_seconds(et(2026, 11, 14, 12, 0)) == 900      # weekend: wake up every 15 min
    assert s.sleep_seconds(et(2026, 11, 16, 9, 29, 0)) == 80    # Monday: open + 20 s


def test_daily_target(cfg, tmp_path):
    s, _ = make(cfg, tmp_path, FakeSource())
    assert str(s.daily_target(et(2026, 11, 10, 10, 0))) == "2026-11-09"
    assert str(s.daily_target(et(2026, 11, 10, 16, 20))) == "2026-11-10"
    assert str(s.daily_target(et(2026, 11, 14, 12, 0))) == "2026-11-13"
    assert str(s.daily_target(et(2026, 11, 27, 13, 20))) == "2026-11-27"  # early close


def test_state_survives_restart(cfg, tmp_path):
    src = FakeSource(daily={"TEST": breakout_daily()})
    s, _ = make(cfg, tmp_path, src)
    s.tick(et(2026, 11, 10, 8, 0))
    s2, n2 = make(cfg, tmp_path, src)
    s2.tick(et(2026, 11, 10, 8, 30))
    assert n2.messages == []  # daily scan already done, nothing resent
    assert s2.state.n_open == 1


def test_no_data_schedules_retry(cfg, tmp_path):
    s, n = make(cfg, tmp_path, FakeSource())
    now = et(2026, 11, 10, 8, 0)
    s.tick(now)
    assert s.daily_retry_after == now + timedelta(minutes=10)
    assert s.state.flag("daily_done") is None


# ---- cloud mode: every run is a fresh process (GitHub Actions) ----

def test_separate_runs_do_not_duplicate(cfg, tmp_path):
    src = FakeSource(intraday={"TEST": intraday_day(bars=orb_bars())})
    for minute in (0, 5, 10):
        s, n = make(cfg, tmp_path, src)
        s.state.set_flag("daily_done", "2026-11-09")
        s.tick(et(2026, 11, 10, 10, minute, 20))
        if minute == 0:
            assert any("GİRİŞ SİNYALİ" in m for m in n.messages)
            assert any("Piyasa açıldı" in m for m in n.messages)
        else:
            assert n.messages == []


def test_delayed_run_still_alerts_within_freshness(cfg, tmp_path):
    src = FakeSource(intraday={"TEST": intraday_day(bars=orb_bars())})
    s, n = make(cfg, tmp_path, src)
    s.state.set_flag("daily_done", "2026-11-09")
    s.tick(et(2026, 11, 10, 10, 12, 0))  # scheduler ran 12 minutes late
    assert any("GİRİŞ SİNYALİ" in m for m in n.messages)


def test_too_late_run_does_not_alert(cfg, tmp_path):
    src = FakeSource(intraday={"TEST": intraday_day(bars=orb_bars())})
    s, n = make(cfg, tmp_path, src)
    s.state.set_flag("daily_done", "2026-11-09")
    s.tick(et(2026, 11, 10, 10, 20, 0))
    assert not any("GİRİŞ SİNYALİ" in m for m in n.messages)
