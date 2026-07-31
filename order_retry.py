# order_retry.py
"""Coada persistenta (outbox) de RE-PLASARE a ordinelor esuate — AGNOSTICA de provider.

Model (varianta B, decizie user): MULTI WRITERI (orice proces care plaseaza, prin
Instrument.place -> enqueue pe esec) + UN SINGUR READER (order_retry_worker.py) care
reia. Store = un JSONL in cachedb/, protejat de un FileLock (cross-proces). Volumul e
mic (esecurile-s rare), deci un lock global pe operatii e suficient.

Ce se salveaza: symbol/side/qty + place_kwargs (safeback/force/cancelorders/hours/smart/
bypass_profit_guard/motivation) + INTENTIA DE PRET (requested_price = pretul cerut de
apelant, ref_price = pretul de piata la esec) + id/created_ts/attempts/last_attempt_ts.
Pretul NU se re-trimite ca valoare fixa — la retry se recalculeaza din pretul CURENT, dar
DOAR daca acesta e in AVANTAJ fata de cel cerut (price_gate_ok). La enqueue se face DEDUP
(o singura intentie pending per symbol+side+banda de pret) + plafon de coada. Astea fac un
TTL lung sigur (intentie persistenta gardata pe pret, nu oarba pe timp). Vezi
order_retry_config.env pt TTL/interval/toleranta-pret/dedup/plafon/kill-switch.

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
# Gard de PRET la retry (31 iul): reia un ordin DOAR cand pretul curent e in AVANTAJ fata
# de cel CERUT initial (SELL: current >= cerut*(1-tol) — astepti sa urce; BUY: current <=
# cerut*(1+tol) — astepti sa scada). Transforma retry-ul din "orb pe timp" in "conditionat
# de pret" -> fara ghost-orders la preturi dezavantajoase + un TTL lung devine sigur.
RETRY_PRICE_TOL = float(os.environ.get("RETRY_PRICE_TOL", "0.002"))
# Dedup la enqueue: NU dubla o intentie deja pending (acelasi symbol+side, pret cerut in
# aceeasi banda). Opreste inundarea cozii de refuzuri de gard repetate ciclu-de-ciclu.
RETRY_DEDUP = os.environ.get("RETRY_DEDUP", "true").strip().lower() == "true"
RETRY_DEDUP_PRICE_TOL = float(os.environ.get("RETRY_DEDUP_PRICE_TOL", "0.003"))
# Plafon DUR pe dimensiunea cozii (centura de siguranta). 0 = fara plafon.
RETRY_MAX_QUEUE = int(float(os.environ.get("RETRY_MAX_QUEUE", "500")))

_ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(_ROOT, "cachedb", "order_retry_queue.jsonl")
LOCK_FILE = os.path.join(_ROOT, "cachedb", "order_retry_queue.lock")


def _ensure_dir():
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)


def _read_nolock():
    """Citeste coada FARA a lua lock-ul (apelantul il detine deja). Linii corupte -> sarite."""
    if not os.path.exists(QUEUE_FILE):
        return []
    items = []
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


def _same_intent(rec, symbol, side, requested_price, tol):
    """True daca `rec` e ACEEASI intentie pending: acelasi symbol+side si pret cerut in
    aceeasi banda (tol). Fara pret pe AMBELE -> egale pe symbol+side; una cu pret si alta
    fara -> DIFERITE (conservator, nu le confunda)."""
    if rec.get("symbol") != symbol:
        return False
    if (rec.get("side") or "").upper() != (side or "").upper():
        return False
    rp = rec.get("requested_price")
    if rp is None and requested_price is None:
        return True
    if rp is None or requested_price is None:
        return False
    try:
        rp = float(rp); reqp = float(requested_price)
    except (TypeError, ValueError):
        return False
    if rp <= 0 or reqp <= 0:
        return rp == reqp
    return abs(rp - reqp) / reqp <= tol


def enqueue(symbol, side, qty, place_kwargs=None, requested_price=None, ref_price=None,
            now=None):
    """Adauga un ordin esuat in coada (sub lock). Intoarce id-ul nou; sau id-ul intrarii
    EXISTENTE daca dedup prinde o intentie identica pending; sau None daca RETRY_ENABLED e
    False / plafonul de coada e atins. Captureaza INTENTIA DE PRET: `requested_price`
    (pretul cerut de apelant) + `ref_price` (pretul de piata la esec) — folosite la retry
    pt gardul de pret + dedup. Pretul NU se re-trimite ca valoare fixa (se recalculeaza
    din pretul curent). `place_kwargs` = kwargs cu care s-a incercat plasarea, refolositi."""
    if not RETRY_ENABLED:
        return None
    now = now if now is not None else time.time()
    side_u = (side or "").upper()
    rec = {
        "id": uuid.uuid4().hex,
        "symbol": symbol,
        "side": side_u,
        "qty": qty,
        "place_kwargs": dict(place_kwargs or {}),
        "requested_price": requested_price,
        "ref_price": ref_price,
        "created_ts": now,
        "attempts": 0,
        "last_attempt_ts": 0.0,
    }
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        if RETRY_DEDUP:
            for e in existing:
                if _same_intent(e, symbol, side_u, requested_price, RETRY_DEDUP_PRICE_TOL):
                    return e.get("id")   # deja pending -> nu dubla intentia
        if RETRY_MAX_QUEUE > 0 and len(existing) >= RETRY_MAX_QUEUE:
            print(f"[order_retry] coada plina ({len(existing)}/{RETRY_MAX_QUEUE}) "
                  f"— NU adaug {side_u} {symbol}")
            return None
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return rec["id"]


def load_all(now=None):
    """Toate intrarile din coada (sub lock). Liniile corupte sunt sarite (defensiv)."""
    _ensure_dir()
    with FileLock(LOCK_FILE):
        return _read_nolock()


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


def price_gate_ok(rec, current_price, tol=None):
    """True daca e MOMENTUL sa reincercam din perspectiva PRETULUI: pretul curent e in
    AVANTAJ fata de cel CERUT initial. SELL: current >= cerut*(1-tol) (astepti sa urce
    inapoi); BUY: current <= cerut*(1+tol) (astepti sa scada). Pret curent None -> False
    (nu putem decide). Fara pret cerut capturat (intrare veche/anormala/market-order) ->
    False, CONSERVATOR: NU reluam orb pe bani reali; intrarea ramane inerta pana la TTL."""
    if current_price is None:
        return False
    req = rec.get("requested_price")
    if req is None:
        return False
    try:
        req = float(req)
    except (TypeError, ValueError):
        return False
    if req <= 0:
        return False
    tol = RETRY_PRICE_TOL if tol is None else tol
    side = (rec.get("side") or "").upper()
    if side == "SELL":
        return current_price >= req * (1.0 - tol)
    if side == "BUY":
        return current_price <= req * (1.0 + tol)
    return True
