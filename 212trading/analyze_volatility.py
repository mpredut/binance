#!/usr/bin/env python3
"""
analyze_volatility.py — analiza volatilitatii unui activ (Yahoo OHLC), ca sa
CALIBREZI config-ul t212_bot (DCA spacing, TP, stop-loss) pe cifre, nu pe ghicite.
Reutilizabil pt orice activ nou.

  python3 analyze_volatility.py RGNT
"""
from __future__ import annotations

import json
import statistics
import sys
import urllib.request


def fetch_ohlc(sym: str, rng: str, interval: str):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read())
    res = (data.get("chart", {}).get("result") or [None])[0]
    if not res:
        return [], {}
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c, v = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    out = []
    for i, t in enumerate(ts):
        if i < len(c) and None not in (o[i], h[i], l[i], c[i]):
            out.append((t, o[i], h[i], l[i], c[i], (v[i] if i < len(v) and v[i] else 0)))
    return out, res.get("meta", {})


def pct(a, b):
    return (a - b) / b * 100 if b else 0.0


def pullbacks(closes):
    """Adancimea retragerilor (% sub varful curent) intr-o serie — pt DCA spacing."""
    peak = closes[0]; dips = []; cur = 0.0
    for c in closes:
        if c >= peak:
            if cur > 0:
                dips.append(cur)
            peak = c; cur = 0.0
        else:
            cur = max(cur, pct(peak, c))
    if cur > 0:
        dips.append(cur)
    return dips


def main() -> int:
    sym = sys.argv[1] if len(sys.argv) > 1 else "RGNT"
    intra, meta = fetch_ohlc(sym, "1d", "5m")
    daily, _ = fetch_ohlc(sym, "1mo", "1d")
    if not intra:
        print(f"! fara date intraday pt {sym} (simbol Yahoo gresit?)"); return 1

    o0 = intra[0][1]; last = intra[-1][4]
    hi = max(b[2] for b in intra); lo = min(b[3] for b in intra)
    closes = [b[4] for b in intra]
    rets = [pct(closes[i], closes[i - 1]) for i in range(1, len(closes))]
    abs_rets = [abs(r) for r in rets]
    bar_rng = [pct(b[2], b[3]) for b in intra]           # (high-low)/low per bara 5m
    dips = pullbacks(closes)

    print(f"========== VOLATILITATE {sym} ==========")
    print(f"  pret curent {last:.2f} {meta.get('currency','')}  | exchange {meta.get('exchangeName','?')}")
    print(f"  AZI: open {o0:.2f} -> last {last:.2f}  ({pct(last,o0):+.1f}%)  | range zi {pct(hi,lo):.1f}% (lo {lo:.2f} / hi {hi:.2f})")
    print(f"  spike max (lo->hi azi): {pct(hi,lo):+.1f}%")
    print(f"\n  --- bare 5min ({len(intra)} bare) ---")
    print(f"  miscare medie/bara : {statistics.mean(abs_rets):.2f}%   (mediana {statistics.median(abs_rets):.2f}%)")
    print(f"  bara 5m p90        : {sorted(abs_rets)[int(len(abs_rets)*0.9)]:.2f}%   max {max(abs_rets):.2f}%")
    print(f"  range mediu/bara   : {statistics.mean(bar_rng):.2f}%  (ATR-5m aprox)")
    if dips:
        ds = sorted(dips)
        print(f"\n  --- retrageri (dip-uri) azi: {len(dips)} ---")
        print(f"  dip median {statistics.median(dips):.2f}%  | p75 {ds[int(len(ds)*0.75)]:.2f}%  | max {max(dips):.2f}%")
    if daily:
        dr = [pct(b[2], b[3]) for b in daily[-10:]]
        dchg = [abs(pct(b[4], b[1])) for b in daily[-10:]]
        print(f"\n  --- zilnic (ultimele {len(dr)} zile) ---")
        print(f"  range mediu/zi {statistics.mean(dr):.1f}%  | miscare medie close-open {statistics.mean(dchg):.1f}%  | max zi {max(dr):.1f}%")

    # --- SUGESTIE PARAMETRI (euristici transparente) ---
    fee = 0.30  # FX 0.15%x2
    med_dip = statistics.median(dips) if dips else statistics.mean(abs_rets) * 2
    atr5 = statistics.mean(bar_rng)
    tp = max(round(med_dip * 1.3, 1), round(fee + 2 * atr5, 1))   # bate fee+zgomot, prinde un swing real
    drop = round(max(med_dip, atr5 * 1.5), 1)                     # DCA la un dip tipic
    daily_rng = statistics.mean([pct(b[2], b[3]) for b in daily[-10:]]) if daily else 10
    stop = round(min(max(daily_rng * 1.3, tp * 2.5), 25), 0)      # > swing zilnic tipic, plafon 25%
    print(f"\n  ===> SUGESTIE (euristic, pt buget $3000):")
    print(f"       TP            = {tp}%   (fee {fee}% + 2xATR5 {2*atr5:.1f}% sau 1.3x dip median)")
    print(f"       DCA_DROP_PCT  = {drop}%")
    print(f"       STOP_LOSS_PCT = {stop:.0f}%")
    print("=========================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
