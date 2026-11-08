"""Günlük stratejileri geçmiş veride test eder.

Kullanım:
  python geriye_test.py                       config.yaml'daki hisseler, son 5 yıl
  python geriye_test.py --yil 10 --kayma-bps 10
  python geriye_test.py --hisseler AAPL NVDA
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tarayici.backtest import baseline_frame, simulate, summarize, trades_frame  # noqa: E402
from tarayici.config import load_config  # noqa: E402
from tarayici.data import YFinanceSource  # noqa: E402
from tarayici.signals import DAILY_STRATEGIES, STRATEGY_NAMES  # noqa: E402


def fmt(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"{name:<34} işlem yok"
    pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return (f"{name:<34} {s['n']:>6} {s['win_rate']:>7.1f}% {s['avg_r']:>+8.3f} "
            f"{s['total_r']:>+9.1f} {pf:>6} {s['max_losing_streak']:>9} {s['avg_days']:>6.1f}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--yil", type=int, default=5)
    ap.add_argument("--kayma-bps", type=float, default=5.0, help="giriş ve çıkışta kayma (baz puan)")
    ap.add_argument("--max-gun", type=int, default=20)
    ap.add_argument("--hisseler", nargs="*")
    args = ap.parse_args()

    cfg = load_config(args.config)
    tickers = [t.upper() for t in args.hisseler] if args.hisseler else cfg["izleme_listesi"]
    print(f"{len(tickers)} hisse için {args.yil} yıllık günlük veri indiriliyor...")
    data = YFinanceSource(daily_period=f"{args.yil}y").daily(tickers)
    if not data:
        print("Veri alınamadı. İnternet bağlantısını kontrol edip tekrar dene.")
        return 1

    results, all_trades = {}, []
    for key, (frame_fn, _) in DAILY_STRATEGIES.items():
        p = cfg["stratejiler"].get(key, {})
        if not p.get("aktif", True):
            continue
        trades = []
        for t, df in data.items():
            trades += simulate(df, frame_fn(df, p, cfg["risk"]), t, key, args.max_gun, args.kayma_bps)
        results[STRATEGY_NAMES[key]] = summarize(trades)
        all_trades += trades
    base = []
    for t, df in data.items():
        base += simulate(df, baseline_frame(df, cfg["risk"]), t, "rastgele", args.max_gun, args.kayma_bps)
    results["Kıyas: her gün rastgele giriş"] = summarize(base)

    print()
    print(f"{'Strateji':<34} {'İşlem':>6} {'İsabet':>8} {'Ort. R':>8} {'Toplam R':>9} {'PF':>6} "
          f"{'Ard.kayıp':>9} {'Gün':>6}")
    print("-" * 95)
    for name, s in results.items():
        print(fmt(name, s))
    print()
    print("Nasıl okunur: Ort. R = işlem başına ortalama kazanç, risk birimi cinsinden.")
    print("+0.20R, her 100 TL riske ortalama 20 TL kazanç demek. Strateji, 'rastgele giriş'")
    print("satırını belirgin şekilde geçmiyorsa sinyal bir avantaj sağlamıyor demektir.")
    print("100'den az işlem varsa sonuç büyük ihtimalle şans; daha çok hisse/yıl ile dene.")
    print("UYARI: Liste bugünün başarılı şirketlerinden oluştuğu için sonuçlar olduğundan iyi")
    print("görünür (hayatta kalma yanılgısı). Komisyon, vergi ve kur farkı dahil değildir.")

    out = HERE / cfg["veri_klasoru"]
    out.mkdir(exist_ok=True)
    if all_trades:
        path = out / "geriye_test_islemler.csv"
        trades_frame(all_trades).to_csv(path, index=False, encoding="utf-8-sig")
        print(f"\nTüm işlemler: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
