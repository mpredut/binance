#!/usr/bin/env bash
# Ruleaza hl_bot.py cu python-ul din venv-ul cu SDK Hyperliquid.
#   ./run.sh --price        ./run.sh --paper        ./run.sh --balance
exec /home/mariusp/binance/.venv/bin/python "$(dirname "$0")/hl_bot.py" "$@"
