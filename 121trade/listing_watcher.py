#!/usr/bin/env python3
"""
listing_watcher.py — componenta GENERICA: "asteapta pana un activ devine tranzactionabil".

Decuplata de strategie si de orice activ anume. Pentru ORICE simbol nou (IPO,
listare proaspata, ticker reactivat) asteapta pana cand chiar se tranzactioneaza
(volum real / pret in miscare), fara sa se pacaleasca de placeholder-ul pre-listare.
Refolosibila de t212_bot.py pentru fiecare activ pe care il astepti.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from ipo_common import ET, log
from market_data import check_market


def in_market_window() -> bool:
    """True intre ~9:00 si ~16:30 ET, zile lucratoare (orele pietei US)."""
    n = datetime.now(ET)
    if n.weekday() >= 5:
        return False
    minutes = n.hour * 60 + n.minute
    return 9 * 60 <= minutes <= 16 * 60 + 30


def _wait(stop: "threading.Event | None", seconds: float) -> None:
    """Sleep intreruptibil daca avem un stop event, altfel simplu."""
    if stop is not None:
        stop.wait(seconds)
    else:
        time.sleep(seconds)


def wait_for_launch(yahoo_symbol: str, label: str, interval: int = 60, *,
                    market_hours_only: bool = False,
                    stop: "threading.Event | None" = None,
                    on_launch=None) -> bool:
    """Blocheaza pana `yahoo_symbol` e LANSAT (a tranzactionat real). Mecanism identic
    pentru orice simbol: deja-listat (NVDA) trece imediat; placeholder pre-IPO (volum 0)
    e asteptat pana se deschide.

    Returneaza True la lansare, False daca `stop` e setat intre timp.
    `on_launch(info)` (optional) e apelat cu dict-ul de piata la lansare (ex. notificare).
    """
    log(f"    [{label}] astept lansarea... (poll {interval}s)")
    while not (stop is not None and stop.is_set()):
        if market_hours_only and not in_market_window():
            _wait(stop, min(interval * 5, 600))
            continue
        m = check_market(yahoo_symbol)
        # 'launched' = a tranzactionat real (are volum), chiar daca piata e inchisa acum.
        if m and m.get("launched"):
            now_open = "se tranzactioneaza ACUM" if m.get("trading") else f"piata {m.get('state')}"
            log(f">>> [{label}] DISPONIBIL pe {m.get('exchange')} ({now_open}) "
                f"pret={m.get('price')} {m.get('currency') or ''} <<<")
            if on_launch:
                try:
                    on_launch(m)
                except Exception as e:  # noqa: BLE001
                    log(f"    ! [{label}] on_launch a esuat: {e}")
            return True
        if m:
            log(f"    [{label}] ping — astept lansarea | pret={m.get('price')} "
                f"vol={m.get('volume')} state={m.get('state')} age={m.get('age_min')}min")
        else:
            log(f"    [{label}] ping — simbol indisponibil pe feed")
        _wait(stop, interval)
    return False
