#!/bin/bash
# Launch the SPCX bot (xStock, NO allocation — a direct buy at listing time).
# Run it when the 🚀 listing alert arrives (it carries the exact pair key):
#   ./spcx_launch.sh SPCXXUSD
# Sume aprobate 12 iun 2026: intrare $800, DCA $500 la -4%, plafon $5.000,
# TP +12%, stop-loss 18%, reintrare doar la -3% sub pretul vandut.
# Theoretical maximum loss per cycle ~ $900 (18% of the $5,000 deployed).
PAIR="${1:?Missing perechea. Ex: ./spcx_launch.sh SPCXXUSD (the key comes in the listing alert)}"
cd "$(dirname "$0")"
STRAT_ENTRY=800 STRAT_DCA=500 STRAT_DCA_DROP_PCT=4 STRAT_TAKEPROFIT_PCT=12 \
STRAT_STOP_LOSS_PCT=12 STRAT_MAX_BUDGET=5000 STRAT_REENTRY_DROP_PCT=3 \
nohup python kraken_bot.py --pair "$PAIR" > spcx_bot.log 2>&1 &
echo "SPCX bot started on $PAIR — log: spcx_bot.log"
