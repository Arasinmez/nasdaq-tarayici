"""Stratejileri geçmiş veride, filtreli ve filtresiz olarak test eder.

Kullanım:
  python geriye_test.py                       config.yaml'daki hisseler, son 5 yıl
  python geriye_test.py --yil 10 --kayma-bps 10
  python geriye_test.py --hisseler AAPL NVDA
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tarayici.backtest import baseline_frame, simulate, summarize, trades_frame  # noqa: E402
from tarayici.config import load_config  # noqa: E402
from tarayici.data import YFinanceSource  # noqa: E402
from tarayici.signals import DAILY_STRATEGIES, STRATEGY_NAMES, filter_mask  # noqa: E402

HEADER = (f"{'':<32} {'İşlem':>6} {'Kazanan':>8} {'TP1':>6} {'Ort. R':>8} {'± hata':>7} "
          f"{'Toplam R':>9} {'PF':>5} {'Gün':>5}")


def fmt(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"{name:<32} işlem yok"
    pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return (f"{name:<32} {s['n']:>6} {s['win_rate']:>7.1f}% {s['tp1_rate']:>5.0f}% "
            f"{s['avg_r']:>+8.3f} {s['margin']:>7.3f} {s['total_r']:>+9.1f} {pf:>5} {s['avg_days']:>5.1f}")


def variant_cfg(cfg: dict, market: bool, rs: bool) -> dict:
    c = copy.deepcopy(cfg)
    c["filtreler"]["piyasa"]["aktif"] = market
    c["filtreler"]["goreceli_guc"]["aktif"] = rs
    return c


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
    index = str(cfg["filtreler"].get("endeks", "QQQ")).upper()
    print(f"{len(tickers)} hisse + {index} için {args.yil} yıllık günlük veri indiriliyor...")
    data = YFinanceSource(daily_period=f"{args.yil}y").daily(tickers + [index])
    mkt = data.pop(index, None)
    if not data:
        print("Veri alınamadı. İnternet bağlantısını kontrol edip tekrar dene.")
        return 1
    if mkt is None:
        print(f"UYARI: {index} verisi alınamadı, filtreli sonuçlar boş çıkacak.")

    variants = [
        ("FİLTRESİZ", variant_cfg(cfg, False, False)),
        ("PİYASA FİLTRESİ (QQQ > 200G)", variant_cfg(cfg, True, False)),
        ("PİYASA + GÖRECELİ GÜÇ", variant_cfg(cfg, True, True)),
    ]
    all_trades = []
    for title, vc in variants:
        print()
        print(f"=== {title} ===")
        print(HEADER)
        masks = {t: filter_mask(df, mkt, vc) for t, df in data.items()}
        for key, (frame_fn, _) in DAILY_STRATEGIES.items():
            p = vc["stratejiler"].get(key, {})
            if not p.get("aktif", True):
                continue
            trades = []
            for t, df in data.items():
                trades += simulate(df, frame_fn(df, p, vc["risk"]), t, key, args.max_gun,
                                   args.kayma_bps, vc["risk"], masks[t])
            print(fmt(STRATEGY_NAMES[key], summarize(trades)))
            if title == variants[-1][0]:
                all_trades += trades
        base = []
        for t, df in data.items():
            base += simulate(df, baseline_frame(df, vc["risk"]), t, "rastgele", args.max_gun,
                             args.kayma_bps, vc["risk"], masks[t])
        print(fmt("Kıyas: rastgele giriş", summarize(base)))

    print()
    print("Nasıl okunur:")
    print("• Ort. R: işlem başına ortalama sonuç. +0.20R = her 100 TL riske ortalama +20 TL.")
    print("• ± hata: şans payı. İki satırın farkı bu paydan küçükse fark şans olabilir.")
    print("• Plan: TP1'de yarısı satılır ve stop girişe çekilir, kalan yarı TP2'de satılır.")
    print("• Bir strateji, aynı filtredeki 'rastgele giriş' satırını belirgin geçmiyorsa")
    print("  sinyal ekstra bir şey katmıyor demektir. Filtrenin katkısını satırlar arası kıyasla gör.")
    print("UYARI: Liste bugünün başarılı şirketlerinden oluştuğu için sonuçlar olduğundan iyi")
    print("görünür (hayatta kalma yanılgısı). Komisyon, vergi ve kur farkı dahil değildir.")

    out = HERE / cfg["veri_klasoru"]
    out.mkdir(exist_ok=True)
    if all_trades:
        path = out / "geriye_test_islemler.csv"
        trades_frame(all_trades).to_csv(path, index=False, encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
