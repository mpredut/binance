# order_retry.py
"""Coada persistenta (outbox) de RE-PLASARE a ordinelor esuate — AGNOSTICA de provider.

Model (varianta B, decizie user): MULTI WRITERI (orice proces care plaseaza, prin
Instrument.place -> enqueue pe esec) + UN SINGUR READER (order_retry_worker.py) care
reia. Store = un JSONL in cachedb/, protejat de un FileLock (cross-proces). Volumul e
mic (esecurile-s rare), deci un lock global pe operatii e suficient.

Ce se salveaza: symbol/side/qty + place_kwargs (safeback/force/cancelorders/hours/smart/
bypass_profit_guard/motivation) + id/created_ts/attempts/last_attempt_ts. NU se salveaza
pretul ca valoare fixa — la retry se recalculeaza din pretul CURENT (in avantaj), trecand
din nou prin pipeline. Vezi order_retry_config.env pt TTL/interval/kill-switch.

Fara dependinte grele (doar stdlib + lock.FileLock + botcore) -> importabil lazy din
Instrument.place fara risc de ciclu.
"""
import os
import json
import time
import uuid
import tempfile

from lock import FileLock

from botcore import load_dotenv as _load_dotenv
_load_dotenv("order_retry_config.env")

RETRY_ENABLED = os.environ.get("RETRY_ENABLED", "true").strip().lower() == "true"
RETRY_INTERVAL_SEC = float(os.environ.get("RETRY_INTERVAL_SEC", "300"))
RETRY_TTL_SEC = float(os.environ.get("RETRY_TTL_SEC", str(24 * 3600)))
RETRY_MAX_ATTEMPTS = int(float(os.environ.get("RETRY_MAX_ATTEMPTS", "0")))

_ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(_ROOT, "cachedb", "order_retry_queue.jsonl")
LOCK_FILE = os.path.join(_ROOT, "cachedb", "order_retry_queue.lock")


def _ensure_dir():
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)


def enqueue(symbol, side, qty, place_kwargs=None, now=None):
    """Adauga un ordin esuat in coada (append atomic sub lock). Intoarce id-ul, sau
    None daca RETRY_ENABLED e False. `place_kwargs` = kwargs cu care s-a incercat
    plasarea (safeback_seconds/force/cancelorders/hours/smart/bypass_profit_guard/
    motivation) — refolositi la retry. Pretul NU se salveaza (recalculat la retry)."""
    if not RETRY_ENABLED:
        return None
    now = now if now is not None else time.time()
    rec = {
        "id": uuid.uuid4().hex,
        "symbol": symbol,
        "side": (side or "").upper(),
        "qty": qty,
        "place_kwargs": dict(place_kwargs or {}),
        "created_ts": now,
        "attempts": 0,
        "last_attempt_ts": 0.0,
    }
    _ensure_dir()
    with FileLock(LOCK_FILE):
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return rec["id"]


def load_all(now=None):
    """Toate intrarile din coada (sub lock). Liniile corupte sunt sarite (defensiv)."""
    _ensure_dir()
    items = []
    with FileLock(LOCK_FILE):
        if not os.path.exists(QUEUE_FILE):
            return items
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except ValueError:
                    continue
    return items


def rewrite(items):
    """Rescrie coada cu `items` (atomic: tmp + os.replace, sub lock). Folosit de reader
    dupa un pas de procesare (scoate succesele/expiratele, actualizeaza attempts)."""
    _ensure_dir()
    with FileLock(LOCK_FILE):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(QUEUE_FILE), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for rec in items:
                    f.write(json.dumps(rec) + "\n")
            os.replace(tmp, QUEUE_FILE)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


def is_expired(rec, now=None):
    """True daca ordinul si-a depasit TTL (renunta) SAU plafonul dur de incercari."""
    now = now if now is not None else time.time()
    if now - float(rec.get("created_ts", 0)) > RETRY_TTL_SEC:
        return True
    if RETRY_MAX_ATTEMPTS > 0 and int(rec.get("attempts", 0)) >= RETRY_MAX_ATTEMPTS:
        return True
    return False


def is_due(rec, now=None):
    """True daca a trecut destul (RETRY_INTERVAL_SEC) de la ultima incercare — sau, pt un
    ordin INCA neincercat (last_attempt_ts=0), de la CREARE (prima reincercare abia dupa
    un interval de la esec, NU imediat -> evita spam de re-esecuri pe refuzuri de gard)."""
    now = now if now is not None else time.time()
    base = max(float(rec.get("last_attempt_ts", 0)), float(rec.get("created_ts", 0)))
    return (now - base) >= RETRY_INTERVAL_SEC
