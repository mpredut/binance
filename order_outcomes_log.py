# order_outcomes_log.py
"""Jurnal FLEET-WIDE de incercari de plasare ordine — un rand pipe-delimited per
incercare, INDIFERENT de provider (Binance, Kraken, Hyperliquid...). Observational:
nu poate afecta returul apelantului (protejat de try/except la scriere).

Extras din binance_api/bapi_placeorder.py (30 iul) — inainte era invocat DOAR din
place_order_smart (Binance), deci ordinele Kraken/HL erau invizibile in
logger/order_outcomes_*.log. Acum reutilizat si de Instrument.place() (vezi
instrument.py), pt orice provider care nu garda intern.

Format NESCHIMBAT (tradeall_observe.py citeste fisierele dupa acest tipar):
    ts|symbol|side|price|qty|outcome|refuse_reason|caller|motivation
"""
import os
import time
from datetime import datetime

ORDER_OUTCOMES_LOG_DIR = "logger"


def _sanitize_outcome_field(value):
    """Elimina caractere care ar sparge formatul pipe-delimited."""
    return str(value).replace("|", "/").replace("\n", " ") if value is not None else ""


def log_order_outcome(symbol, side, price, qty, outcome, refuse_reason, motivation, caller=None):
    """caller: eticheta descriptiva a apelantului (ex. numele fisierului care a initiat
    plasarea) — calculata de FIECARE call site (bapi_placeorder, Instrument.place), nu
    aici, ca sa nu depindem de o adancime fixa de stack (diferita intre apelanti)."""
    try:
        os.makedirs(ORDER_OUTCOMES_LOG_DIR, exist_ok=True)
        path = os.path.join(ORDER_OUTCOMES_LOG_DIR,
                             f"order_outcomes_{datetime.now().strftime('%Y-%m-%d')}.log")
        cols = [time.time(), symbol, side, price, qty, outcome,
                refuse_reason or "", caller or "", motivation or ""]
        line = "|".join(_sanitize_outcome_field(c) for c in cols)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[log_order_outcome] eroare scriere jurnal outcome: {e}")
