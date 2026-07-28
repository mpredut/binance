#!/usr/bin/env bash
# run_backtest_parallel.sh — ruleaza backtesturi IN PARALEL pe dev (N core-uri).
# Backtestul e CPU-bound si independent per param => embarrassingly parallel.
#
# Usage: run_backtest_parallel.sh <fisier_comenzi> [JOBS]
#   <fisier_comenzi>: o comanda pe linie (# = comentariu, liniile goale ignorate).
#   JOBS: cate deodata (implicit nproc).
# Fiecare comanda ruleaza in propriul proces, cu output in
#   logs/backtest_parallel/<timestamp>/NN.log  (+ manifest.tsv cu maparea NN->comanda).
#
# Ruleaza pe DEV (masina de backtest). NU atinge configul/procesele live.
set -uo pipefail

ROOT="${ROOT:-$HOME/binance}"; cd "$ROOT"
CMDFILE="${1:?dai un fisier cu comenzi (una pe linie)}"
JOBS="${2:-$(nproc)}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTDIR="$ROOT/logs/backtest_parallel/$STAMP"; mkdir -p "$OUTDIR"

mapfile -t CMDS < <(grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$CMDFILE")
echo "[parallel] ${#CMDS[@]} comenzi, JOBS=$JOBS -> $OUTDIR"

idx=0
for cmd in "${CMDS[@]}"; do
  idx=$((idx + 1))
  log="$OUTDIR/$(printf '%02d' "$idx").log"
  printf '%02d\t%s\n' "$idx" "$cmd" | tee -a "$OUTDIR/manifest.tsv" >/dev/null
  echo "=== [$idx] $cmd ===" > "$log"
  ( eval "$cmd" >> "$log" 2>&1; echo "[exit $?]" >> "$log" ) &
  # limiteaza concurenta la JOBS
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
done
wait

echo "[parallel] GATA. Loguri in $OUTDIR"
echo "[parallel] manifest:"; cat "$OUTDIR/manifest.tsv" | sed 's/^/  /'
