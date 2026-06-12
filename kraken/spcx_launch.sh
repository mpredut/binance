#!/bin/bash
# Lansare bot SPCX (xStock, FARA alocare — cumparare directa la listare).
# Ruleaza cand vine alerta 🚀 de listare (contine cheia exacta a perechii):
#   ./spcx_launch.sh SPCXXUSD
# Sume aprobate 12 iun 2026: intrare $800, DCA $500 la -4%, plafon $5.000,
# TP +12%, stop-loss 18%, reintrare doar la -3% sub pretul vandut.
# Pierdere maxima teoretica pe ciclu ~ $900 (18% din $5.000 desfasurat).
PAIR="${1:?Lipseste perechea. Ex: ./spcx_launch.sh SPCXXUSD (cheia vine in alerta de listare)}"
cd "$(dirname "$0")"
STRAT_ENTRY=800 STRAT_DCA=500 STRAT_DCA_DROP_PCT=4 STRAT_TAKEPROFIT_PCT=12 \
STRAT_STOP_LOSS_PCT=7 STRAT_MAX_BUDGET=5000 STRAT_REENTRY_DROP_PCT=3 \
nohup python kraken_bot.py --pair "$PAIR" > spcx_bot.log 2>&1 &
echo "Bot SPCX pornit pe $PAIR — log: spcx_bot.log"
