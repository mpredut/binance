#!/usr/bin/env python3
"""order_retry_worker.py — READER-ul UNIC al cozii de re-plasare (outbox order_retry.py).

Model (varianta B): multi writeri (Instrument.place -> order_retry.enqueue pe esec) + UN
SINGUR reader = acest proces. Parcurge coada, si pt fiecare ordin SCADENT (a trecut
RETRY_INTERVAL_SEC de la ultima incercare) verifica GARDUL DE PRET (oq.price_gate_ok:
pretul curent e in avantaj fata de cel cerut?) — daca NU, il lasa in coada fara sa-l
numere ca incercare (asteapta sa revina pretul). Daca DA, il REIA prin mkt.place() la PRET
CURENT, cu is_retry=True (ca sa NU se re-enqueue-ze). Succes -> scos din coada. Esec ->
attempts++/last_attempt, ramane. Peste TTL (sau plafon incercari) -> scos + ALERTA
(renuntare, interventie manuala).

`process_once` e logica PURA (mkt injectabil) -> testabila fara retea. `main` = bucla +
single-instance + supervizare (procs.conf). Kill-switch: RETRY_ENABLED=false (order_retry_config.env).
"""
import os
import sys
import time
import argparse

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import order_retry as oq
import alertnotifiers as alert
from botcore import single_instance

WORKER_POLL_SEC = float(os.environ.get("RETRY_WORKER_POLL_SEC", "30"))


def process_once(mkt, now=None):
    """Un pas de golire a cozii. `mkt` = facada guardata (are get_current_price + place).
    SCHEMA "scoate-inainte-de-plasare": selecteaza ordinele SCADENTE + favorabile (pret in
    avantaj), le SCOATE din coada (claim atomic) INAINTE de a le plasa — ca sa nu fie
    reincercate de nimeni cat timp sunt in curs — apoi le plaseaza la pret CURENT
    (is_retry=True). Succes -> raman scoase. Esec -> RE-adaugate (pastrand vechimea +
    attempts+1). Expirate -> scoase + alerta. Intoarce un dict de statistici."""
    now = now if now is not None else time.time()
    snapshot = oq.load_all(now)

    to_retry = []       # (rec, price) — scadente + favorabile la pret
    expired = []
    skipped_price = 0   # scadente dar cu pretul inca dezavantajos vs cel cerut
    for r in snapshot:
        if oq.is_expired(r, now):
            expired.append(r)
            continue
        if not oq.is_due(r, now):
            continue
        symbol = r.get("symbol")
        try:
            price = mkt.get_current_price(symbol)
        except Exception as e:  # noqa: BLE001
            print(f"[order_retry] pret indisponibil {symbol} ({e}) — sar, ramane in coada")
            continue
        if price is None:
            continue   # pret indisponibil -> lasa in coada, reincearca data viitoare
        # GARD DE PRET: reia doar cand pretul curent e in AVANTAJ fata de cel cerut initial.
        # Altfel lasa in coada (fara a numara ca incercare) — asteapta sa revina pretul,
        # pana la TTL. Evita ghost-orders la preturi proaste + reduce churn-ul pe pipeline.
        if not oq.price_gate_ok(r, price):
            skipped_price += 1
            continue
        to_retry.append((r, price))

    # SCOATE din coada expiratele + cele de reincercat INAINTE de plasare (claim atomic).
    oq.claim([r["id"] for r in expired] + [r["id"] for (r, _) in to_retry], now)

    for r in expired:
        _alert_giveup(r, now)   # deja scoase din coada

    succeeded = 0
    for (r, price) in to_retry:
        symbol = r.get("symbol")
        kwargs = dict(r.get("place_kwargs") or {})
        kwargs["is_retry"] = True   # NU re-enqueue automat din pipeline (o facem noi la esec)
        try:
            order = mkt.place(symbol, r.get("side"), price, r.get("qty"), **kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"[order_retry] retry {r.get('side')} {symbol} a aruncat ({e}) — tratez ca esec")
            order = None
        if order:
            succeeded += 1
            print(f"[order_retry] RE-PLASAT cu succes {r.get('side')} {symbol} @ {price}")
        else:
            # esec -> RE-adauga in coada, pastrand vechimea (created_ts) + attempts+1.
            # Trece prin dedup (symbol+side): daca un plasator a re-cerut intre timp, se
            # comaseaza intr-o singura intentie.
            oq.enqueue(symbol, r.get("side"), r.get("qty"),
                       place_kwargs=r.get("place_kwargs"),
                       requested_price=r.get("requested_price"),
                       ref_price=r.get("ref_price"),
                       now=now,
                       created_ts=r.get("created_ts"),
                       attempts=int(r.get("attempts", 0)) + 1,
                       last_attempt_ts=now)

    return {"attempted": len(to_retry),
            "succeeded": succeeded,
            "expired": len(expired),
            "skipped_price": skipped_price,
            "remaining": len(oq.load_all(now))}


def _alert_giveup(rec, now):
    """Notificare la renuntare (TTL/plafon depasit) — interventie manuala."""
    age_h = (now - float(rec.get("created_ts", now))) / 3600.0
    try:
        alert.notify(
            title=f"🛑 order-retry RENUNT {rec.get('side')} {rec.get('symbol')}",
            body=(f"Ordin nereplasat dupa {rec.get('attempts', 0)} incercari / {age_h:.1f}h "
                  f"(TTL depasit). qty={rec.get('qty')}. Verifica manual."),
            source="order_retry", symbol=str(rec.get("symbol")))
    except Exception as e:  # noqa: BLE001
        print(f"[order_retry] alerta giveup esuata (ignor): {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="un singur pas si iese")
    args = ap.parse_args()

    if args.once:
        if not oq.RETRY_ENABLED:
            print("[order_retry] RETRY_ENABLED=false — nimic de facut (--once).")
            return 0
        from providers.market_api import api as mkt
        print(f"[order_retry] pas unic: {process_once(mkt)}")
        return 0

    single_instance("order_retry_worker")

    # Kill-switch: cand e dezactivat NU iesim (altfel flota_start ne tot reporneste
    # = flapping + alerte pe telefon). Ramanem VII dar inactivi. Re-enable cere
    # oricum un restart al procesului (RETRY_ENABLED e citit la import), deci idle-ul
    # tine pana la urmatorul restart de flota, fara sa spameze supervizarea.
    if not oq.RETRY_ENABLED:
        print("[order_retry] RETRY_ENABLED=false — worker VIU dar inactiv (idle).")
        while True:
            time.sleep(60)

    from providers.market_api import api as mkt

    print(f"[order_retry] start (poll={WORKER_POLL_SEC:.0f}s, interval={oq.RETRY_INTERVAL_SEC:.0f}s, "
          f"TTL={oq.RETRY_TTL_SEC:.0f}s)")
    while True:
        try:
            stats = process_once(mkt)
            if stats["attempted"] or stats["expired"]:
                print(f"[order_retry] {stats}")
        except Exception as e:  # noqa: BLE001
            print(f"[order_retry] eroare in pas (continui): {e}")
        time.sleep(WORKER_POLL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
