#!/usr/bin/env python3
"""order_retry_worker.py — the SINGLE READER of the order_retry.py replacement outbox.

Model B uses multiple writers (Instrument.place -> order_retry.enqueue on failure) and
ONE reader: this process. It scans the queue and, for every DUE order whose
RETRY_INTERVAL_SEC has elapsed, checks oq.price_gate_ok to determine whether the current
price is more favorable than the requested price. If not, the order remains queued
without counting an attempt while it waits for price to recover. If favorable, the worker
retries through mkt.place() at CURRENT PRICE with caller_owns_retry=True to prevent an
automatic re-enqueue. Success removes the record. Failure increments attempts and
last_attempt while retaining it. Exceeding TTL or the attempt limit removes it and sends
an alert for manual intervention.

``process_once`` has an injectable market facade for tests. It still mutates the
persistent queue and sends alerts. The claim-before-submit design prevents concurrent
retries but leaves a crash window in which a claimed intent can be lost.

``main`` adds the single-instance loop and supervision. ``RETRY_ENABLED=false`` is
the configured kill switch.
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
    """Drain one queue step using guarded facade `mkt` (get_current_price + place).
    The claim-before-place scheme selects DUE orders with favorable prices and atomically
    removes them BEFORE placement, preventing another retry while they are in flight. It
    then places them at CURRENT PRICE with caller_owns_retry=True. Successful records stay
    removed; failed records are re-added with their original age and attempts+1. Expired
    records are removed and alerted. A process exit after ``claim`` and before re-enqueue
    loses the claimed record. Return processing statistics."""
    now = now if now is not None else time.time()
    snapshot = oq.load_all(now)

    to_retry = []       # (record, price): due and price-favorable
    expired = []
    skipped_price = 0   # due, but current price is still worse than requested
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
            continue   # unavailable price: retain in queue and retry next time
        # PRICE GUARD: retry only when current price is BETTER than originally requested.
        # Otherwise retain the record without counting an attempt until price recovers or
        # TTL expires. This prevents ghost orders at unfavorable prices and reduces churn.
        if not oq.price_gate_ok(r, price):
            skipped_price += 1
            continue
        to_retry.append((r, price))

    # Atomically claim expired and retryable records by removing them BEFORE placement.
    oq.claim([r["id"] for r in expired] + [r["id"] for (r, _) in to_retry], now)

    for r in expired:
        _alert_giveup(r, now)   # already removed from the queue

    succeeded = 0
    for (r, price) in to_retry:
        symbol = r.get("symbol")
        kwargs = dict(r.get("place_kwargs") or {})
        kwargs["caller_owns_retry"] = True  # the worker explicitly re-adds failures
        try:
            order = mkt.place(symbol, r.get("side"), price, r.get("qty"), **kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"[order_retry] retry {r.get('side')} {symbol} a aruncat ({e}) — tratez ca esec")
            order = None
        if order:
            succeeded += 1
            print(f"[order_retry] RE-PLASAT cu succes {r.get('side')} {symbol} @ {price}")
        else:
            # On failure, re-add the record with its original created_ts and attempts+1.
            # Deduplication by symbol+side merges any placement request made in the interim
            # into a single intent.
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
    """Notify when TTL or the attempt cap is exceeded and manual action is required."""
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

    # When the kill switch is disabled, stay alive instead of exiting; otherwise
    # flota_start repeatedly restarts the worker, causing flapping and phone alerts.
    # Re-enabling already requires a process restart because RETRY_ENABLED is read at
    # import, so remain idle until the next fleet restart without spamming supervision.
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
