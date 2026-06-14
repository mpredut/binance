#!/bin/bash
# healthcheck.sh — verificare consolidata READ-ONLY: procese + conturi (HL/Kraken/T212).
# Ruleaza pe server oricand:  ./healthcheck.sh
ROOT="$(cd "$(dirname "$0")" && pwd)"
# python cu SDK Hyperliquid (eth_account): prefera myenv, cade pe python3
HLPY="$ROOT/myenv/bin/python"
{ [ -x "$HLPY" ] && "$HLPY" -c "import eth_account" 2>/dev/null; } || HLPY=python3

echo "============ HEALTHCHECK $(date '+%Y-%m-%d %H:%M') ============"
echo "=== PROCESE (etime = de cat ruleaza) ==="
ps -eo etime,args | grep -E "dn_bot|kraken_bot|xstock_watch|ipo.py|trailing_stop|cacheManager|priceAnalysis|tradeall|rtrade|monitortrades|run_price_monitor|assetguardian" | grep -v grep

echo "=== HYPERLIQUID DN ==="
( cd "$ROOT/hyperliquid" && "$HLPY" dn_bot.py --status 2>&1 | grep -E "SPOT|PERP|DELTA|FUNDING|LICHIDARE|COLATERAL" )

echo "=== KRAKEN ==="
( cd "$ROOT/kraken" && python3 - <<'PY' 2>/dev/null
import sys, os; sys.path.insert(0, ".")
from common import load_dotenv
load_dotenv(".env"); load_dotenv("config.env")
from kraken_client import KrakenClient
try:
    from xstock_watch import yahoo_last
except Exception:
    yahoo_last = lambda s: None
c = KrakenClient(os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET"))
b = c.balance()
print("  cash ZUSD %.0f + USDC %.0f | HYPE %s @ %s" % (
    float(b.get("ZUSD", 0)), float(b.get("USDC", 0)), b.get("HYPE"), c.last_price("HYPEUSD")))
oo = c.open_orders()
print("  ordine: %d %s" % (len(oo), [o.get("descr", {}).get("order") for o in oo.values()]))
sp = float(b.get("SPCXx.T", 0))
if sp:
    px = yahoo_last("SPCX") or 0
    print("  SPCXx.T %.4f @ %.2f -> $%.0f" % (sp, px, sp * px))
PY
)

echo "=== T212 ==="
( cd "$ROOT/121trade" && python3 - <<'PY' 2>/dev/null
import sys, os, time; sys.path.insert(0, ".")
from ipo_common import load_dotenv
load_dotenv(".env")
from t212_client import T212Client
c = T212Client(os.environ["T212_API_KEY"], os.environ.get("T212_API_SECRET"), env="live")
pf = None
for _ in range(3):
    pf = c.get_portfolio()
    if pf:
        break
    time.sleep(2)
for p in (pf or []):
    if any(s in p.get("ticker", "") for s in ("NVDA", "SPCX")):
        print("  %s qty %s avg %s pret %s P&L %s" % (
            p.get("ticker"), p.get("quantity"), p.get("averagePrice"),
            p.get("currentPrice"), p.get("ppl")))
if not pf:
    print("  portofoliu indisponibil")
PY
)
echo "============ END ============"
