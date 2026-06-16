# instruments_config.py
"""Loader pt `instruments.conf` -> dict[name, Instrument].

Registru CENTRAL, multi-consumator: monitortrades (mt.*), tradeall (tradeall.*),
rtrade (rtrade.*) citesc ACELASI fisier; fiecare ia CORE + namespace-ul lui.

CORE per sectiune: provider, symbol, base, quote, enabled, isolation, market_hours.
Orice alta cheie (ex. 'mt.gain', 'tradeall.budget') intra in `params` ca string;
consumatorul o citeste tipat cu Instrument.param(consumer, key, cast=...).

Lipsa fisierului -> {} (consumatorul cade pe valorile lui implicite). Behavior-preserving:
pana cand un consumator chiar citeste de aici, nimic nu se schimba.
"""
import os
import configparser
from typing import Dict, Optional

from instrument import Instrument

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instruments.conf")

# Chei tratate ca metadate CORE (restul -> params namespaced).
_CORE = {"provider", "symbol", "base", "quote", "enabled", "isolation", "market_hours"}

_TRUE = {"1", "yes", "true", "da", "on"}


def _as_bool(s, default=True) -> bool:
    if s is None:
        return default
    return str(s).strip().lower() in _TRUE


def load_instruments(path: Optional[str] = None, api=None) -> Dict[str, Instrument]:
    """Construieste instrumentele din config. Cheie = numele sectiunii (ex 'TAO_BINANCE')."""
    path = path or DEFAULT_PATH
    out: Dict[str, Instrument] = {}
    if not os.path.exists(path):
        return out
    cp = configparser.ConfigParser()
    # pastreaza cheile cum sunt scrise (configparser le lasa lowercase oricum; ale
    # noastre sunt deja lowercase, deci e ok).
    cp.read(path)
    for section in cp.sections():
        d = dict(cp.items(section))
        if "provider" not in d or "symbol" not in d:
            raise ValueError(f"instruments.conf [{section}]: lipseste 'provider' sau 'symbol'")
        params = {k: v for k, v in d.items() if k not in _CORE}
        out[section] = Instrument(
            name=section,
            symbol=d["symbol"].strip(),
            provider=d["provider"].strip(),
            base=(d.get("base") or "").strip() or None,
            quote=(d.get("quote") or "").strip() or None,
            enabled=_as_bool(d.get("enabled"), True),
            isolation=(d.get("isolation") or "own_ledger").strip(),
            market_hours=(d.get("market_hours") or "24x7").strip(),
            params=params,
            api=api,
        )
    return out


def load_for(consumer: str, path: Optional[str] = None, api=None,
             only_enabled: bool = True) -> Dict[str, Instrument]:
    """Instrumentele relevante pt un consumator (ex. 'mt'): cele cu cel putin o cheie
    in namespace-ul lui, optional doar cele enabled. Comoditate pt monitortrades/tradeall/rtrade."""
    pref = consumer + "."
    res = {}
    for name, inst in load_instruments(path, api).items():
        if only_enabled and not inst.enabled:
            continue
        if any(k.startswith(pref) for k in inst.params):
            res[name] = inst
    return res
