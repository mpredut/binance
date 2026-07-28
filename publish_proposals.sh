#!/usr/bin/env bash
# publish_proposals.sh — publica backtest_proposals.json (scris de scheduled_pilot
# --propose) pe branch-ul git dedicat `backtest-proposals`, prin push. Ruleaza pe
# DEV. Foloseste un git WORKTREE separat (~/binance-proposals) ca sa NU atinga
# working-tree-ul de pe main (unde ruleaza backtestele).
#
# Fluxul (UNIFIED_BACKTEST_PLAN.md §9): dev -> github (branch propuneri) -> prod
# le trage si aplica cu guardrail-uri. Fisierul e gitignored pe main; pe branch-ul
# de propuneri e track-uit cu `git add -f`.
set -euo pipefail

ROOT="${ROOT:-$HOME/binance}"
WT="${WT:-$HOME/binance-proposals}"
BRANCH="backtest-proposals"
SRC_FILE="$ROOT/backtest_proposals.json"

[ -f "$SRC_FILE" ] || { echo "[publish] lipseste $SRC_FILE — ruleaza intai pilotul --propose"; exit 1; }

git -C "$ROOT" fetch -q origin || true

# Creeaza worktree-ul daca lipseste: din origin/$BRANCH daca exista pe remote,
# altfel un branch nou pornit din origin/main.
if ! git -C "$ROOT" worktree list --porcelain | grep -q "worktree $WT"; then
  rm -rf "$WT"
  if git -C "$ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    git -C "$ROOT" worktree add -q "$WT" "$BRANCH"
  else
    git -C "$ROOT" worktree add -q -b "$BRANCH" "$WT" origin/main
  fi
else
  git -C "$WT" pull -q --ff-only origin "$BRANCH" 2>/dev/null || true
fi

cp "$SRC_FILE" "$WT/backtest_proposals.json"
git -C "$WT" add -f backtest_proposals.json
if git -C "$WT" diff --cached --quiet; then
  echo "[publish] propuneri neschimbate — nimic de pushuit"
else
  n=$(grep -c '"full_key"' "$WT/backtest_proposals.json" || true)
  git -C "$WT" commit -q -m "backtest proposals $(date '+%F %T') [$(git -C "$ROOT" rev-parse --short HEAD)] — $n propunere(i)"
  git -C "$WT" push -q origin "$BRANCH"
  echo "[publish] $n propunere(i) publicate pe branch $BRANCH"
fi
