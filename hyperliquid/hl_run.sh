#!/usr/bin/env bash
# Ruleaza hl_bot.py (strategia directionala HL) cu venv-ul care are SDK-ul
# Hyperliquid (eth_account). Portabil: myenv (server) -> .venv (local) -> python3.
#   ./hl_run.sh --price     ./hl_run.sh --paper     ./hl_run.sh --balance
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/../myenv/bin/python"
[ -x "$PY" ] || PY="$HERE/../.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" "$HERE/hl_bot.py" "$@"
