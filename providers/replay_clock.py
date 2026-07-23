# providers/replay_clock.py
"""SimClock — ceasul simulat comun pt backtest/replay (23 iul,
research/UNIFIED_BACKTEST_PLAN.md, pas de unificare tradeall+monitortrades).

Extras din tradeall_backtest.py (unde traia ca `_SimClock`, privat) — acum
partajat, ca ORICE viitor cod de replay (tradeall, monitortrades, sau un
al treilea modul) sa foloseasca ACELASI mecanism de "timp simulat", nu cate
o reimplementare proprie. Interfata ramane identica cu originalul (`__call__`
intoarce `.ts`), deci tradeall_backtest.py il poate importa fara nicio
schimbare de comportament.

Principiu (deja stabilit azi pt monitortrades.ReplayMarketDataProvider):
timpul "vine din pretul obtinut" — driver-ul de replay seteaza `.ts` la
timestamp-ul FIECARUI tick redat, nu un ceas care avanseaza independent."""


import time


class SimClock:
    """Timpul SIMULAT (al tick-ului replay-uit curent), NU ceasul real.
    Pasat ca `now_fn`/`now` catre orice cod care are nevoie de "acum"
    (ex. TrendState(now_fn=clock), monitor_price_and_trade(now_fn=clock)).

    Default `.ts=time.time()` la constructie (identic cu `_SimClock` originalul
    din tradeall_backtest.py) — in practica nu conteaza, driver-ul de replay
    seteaza `.ts` la timestamp-ul primului tick INAINTE ca ceva sa citeasca
    ceasul, dar pastrat identic ca sa nu introduca nicio diferenta observabila."""

    def __init__(self):
        self.ts = time.time()

    def __call__(self) -> float:
        return self.ts
