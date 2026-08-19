"""Reguli PURE de decizie ale strategiei Kraken (DCA + take-profit + stop-loss +
reintrare), partajate de motorul LIVE (kraken/strategy.py, event-driven) si de
backtest (kraken/backtest.py, pe OHLC). Fara stare, fara client, fara I/O — o
SINGURA sursa de adevar pentru pragurile de pret, ca sa nu mai divergheze.

Motivatie: reintrarea STOP-aware (4 aug) a trebuit editata in AMBELE fisiere
(strategy.py step() + backtest.py simulate()) cu `sl_bounce_pct` de doua ori ->
risc de drift. Formulele de aici sunt IDENTICE cu cele din live (sursa de adevar);
apelantii isi pastreaza bucla proprie (event-driven vs OHLC) si contabilitatea
(qty/spent/counts), dar toti trec prin aceleasi praguri de pret.
"""


def diff_percent(v1: float, v2: float) -> float:
    """Diferenta procentuala simetrica (raportata la media absoluta) — identic cu
    botcore.diff_percent, dar self-contained (backtest-ul ruleaza izolat)."""
    if v1 == 0 and v2 == 0:
        return 0.0
    return abs(v1 - v2) / ((abs(v1) + abs(v2)) / 2) * 100


def are_close(v1: float, v2: float, tol_pct: float) -> bool:
    """True daca v1 e la cel mult tol_pct%% de v2 (identic cu botcore.are_close).
    Prag de pret 'aproape de prag' = atins (nu ratam intrari la 2-3 centi de prag)."""
    return diff_percent(v1, v2) <= tol_pct


def entry_price(close: float, disc_pct: float) -> float:
    """Pretul de intrare/DCA: discount `disc_pct`%% sub close."""
    return close * (1 - disc_pct / 100)


def tp_price(avg: float, tp_pct: float) -> float:
    """Pretul de take-profit: `tp_pct`%% peste pretul mediu."""
    return avg * (1 + tp_pct / 100)


def hit_stop(avg: float, price: float, sl_pct: float) -> bool:
    """True daca pierderea nerealizata (long) >= stop-loss. sl_pct<=0 sau avg lipsa
    => False (stop dezactivat)."""
    if sl_pct <= 0 or not avg:
        return False
    return (avg - price) / avg * 100 >= sl_pct


def reentry_stop_blocked(price: float, sl_low: float, bounce_pct: float, tol_pct: float) -> bool:
    """Dupa STOP-LOSS: BLOCAT pana pretul urca cu `bounce_pct`%% peste minimul de
    dupa vanzare (revenire). True = NU reintra inca."""
    prag = sl_low * (1 + bounce_pct / 100)
    return price < prag and not are_close(price, prag, tol_pct)


def reentry_drop_blocked(price: float, last_sell: float, drop_pct: float, tol_pct: float) -> bool:
    """Dupa TP: BLOCAT pana pretul scade cu `drop_pct`%% sub pretul vandut (nu
    recumpara mai sus). True = NU reintra inca. drop_pct<=0 sau last_sell lipsa
    => False (fara bariera)."""
    if drop_pct <= 0 or not last_sell:
        return False
    prag = last_sell * (1 - drop_pct / 100)
    return price > prag and not are_close(price, prag, tol_pct)


def dca_price_hit(price: float, last_buy: float, drop_pct: float, tol_pct: float) -> bool:
    """True daca pretul a scazut cu `drop_pct`%% sub ultima cumparare ('aproape de
    prag' conteaza ca atins). DOAR conditia de PRET — plafoanele de numar DCA / buget
    / ordine deschise raman la apelant. tol_pct=0 => fara toleranta (doar <=)."""
    if not last_buy:
        return False
    prag = last_buy * (1 - drop_pct / 100)
    return price <= prag or are_close(price, prag, tol_pct)
