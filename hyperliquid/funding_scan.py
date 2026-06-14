#!/usr/bin/env python3
"""
funding_scan.py — scanner de funding pe perp-urile Hyperliquid pentru DN.

Amplifica singurul edge real (funding): in loc de DN mereu pe HYPE (~11%/an),
iti arata care monede LICHIDE platesc cel mai mult funding ACUM, ca sa indrepti
DN-ul spre cel mai bun. ADVISORY — nu comuta singur (schimbarea monedei DN =
inchidere+redeschidere, bani reali + riscul noii monede; decizi tu).

DN cere AMBELE picioare: short perp (funding) + long SPOT. Deci doar monedele cu
pereche spot pe HL sunt 'DN-fezabile' (marcate ✓).

ATENTIE: funding mare se coreleaza cu RISC mare (longii euforici pe un alt care
pompeaza -> short-ul tau risca lichidare daca pompa continua). Alege printre
monede lichide vetate, nu 'cel mai mare orbeste'.

  /home/mariusp/binance/.venv/bin/python funding_scan.py
  ...funding_scan.py --min-vol 20000000 --top 20
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def rank(universe, ctxs, min_vol_usd, top=20):
    """Pur (testabil): filtreaza pe volum, claseaza pe funding anualizat desc."""
    rows = []
    for i, a in enumerate(universe):
        c = ctxs[i] if i < len(ctxs) else {}
        try:
            f = float(c.get("funding") or 0)
            vol = float(c.get("dayNtlVlm") or 0)
            mark = float(c.get("markPx") or 0)
            oi = float(c.get("openInterest") or 0)
        except (TypeError, ValueError):
            continue
        if vol < min_vol_usd or f <= 0:           # vrem funding POZITIV (incasezi ca short)
            continue
        rows.append({"coin": a.get("name"), "apr": f * 24 * 365 * 100,
                     "funding_hr_pct": f * 100, "vol": vol, "oi_usd": oi * mark, "mark": mark})
    rows.sort(key=lambda r: -r["apr"])
    return rows[:top]


def _spot_tokens(client) -> set:
    """Tokenii care au pereche spot TOKEN/USDC pe HL (pt long-ul DN)."""
    try:
        m = client.info.spot_meta()
        tokens = {t.get("name"): t.get("index") for t in m.get("tokens", [])}
        usdc = tokens.get("USDC")
        ok = set()
        for u in m.get("universe", []):
            pair = u.get("tokens") or []
            if len(pair) == 2 and usdc in pair:
                other = pair[0] if pair[1] == usdc else pair[1]
                for name, idx in tokens.items():
                    if idx == other:
                        ok.add(name)
        return ok
    except Exception:  # noqa: BLE001
        return set()


def main() -> int:
    ap = argparse.ArgumentParser(description="Scanner funding DN pe Hyperliquid.")
    ap.add_argument("--min-vol", type=float, default=10_000_000, help="volum minim 24h (USD)")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    from hl_client import HLClient
    client = HLClient()
    meta, ctxs = client.info.meta_and_asset_ctxs()
    rows = rank(meta["universe"], ctxs, args.min_vol, args.top)
    spot_ok = _spot_tokens(client)
    cur = os.environ.get("HL_COIN", "HYPE")

    print(f"=== FUNDING SCAN Hyperliquid (vol 24h >= ${args.min_vol/1e6:.0f}M) ===")
    print("  #  moneda    funding/an   funding/ora   vol 24h    DN-fezabil   ")
    for i, r in enumerate(rows, 1):
        dn = "✓ spot" if r["coin"] in spot_ok else "✗ fara spot"
        mark = " <-- DN-ul tau acum" if r["coin"] == cur else ""
        print(f"  {i:<2d} {r['coin']:<8s}  {r['apr']:+6.1f}%/an   {r['funding_hr_pct']:+.4f}%/h   "
              f"${r['vol']/1e6:6.1f}M   {dn:<11s}{mark}")
    # context: unde e HYPE
    hype = next((r for r in rank(meta['universe'], ctxs, 0, 999) if r['coin'] == cur), None)
    if hype:
        print(f"\n  {cur} (DN-ul tau): {hype['apr']:+.1f}%/an. "
              f"Daca o moneda DN-fezabila de mai sus plateste vizibil mai mult SI e lichida,")
        print("  poti muta DN-ul acolo: schimbi HL_COIN in config.env + inchizi/redeschizi DN-ul.")
    print("\n  ⚠ funding mare = risc mai mare. Alege lichid + monede vetate, nu maximul orb.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
