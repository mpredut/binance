# instruments_config.py
"""Load ``instruments.conf`` into ``dict[str, Instrument]``.

``monitortrades``, ``cacheManager``, and ``priceAnalysis`` currently read the ``mt``
namespace. Verification tools also read it. ``tradeall`` and ``rtrade`` retain their
own configuration files and do not import this loader.

Each section's core fields are ``provider``, ``symbol``, ``base``, ``quote``,
``enabled``, ``isolation``, and ``market_hours``. Other values remain strings in
Instrument.params. The registry is authoritative: a missing or malformed
configuration raises instead of silently creating an incomplete instrument.
"""
import os
import configparser
from typing import Dict, Optional

# ``Instrument`` pulls in the heavy provider chain (providers.market_api). It is
# imported lazily inside load_instruments so the lightweight derivation helpers
# below (binance_symbols/trail_pct_map/tradeall_trade_symbols) can be used by
# import-sensitive, fleet-wide modules such as symbols.py without dragging in the
# whole market_api stack or risking an import cycle.

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instruments.conf")

# Keys treated as core metadata; every other key is a namespaced parameter.
_CORE = {"provider", "symbol", "base", "quote", "enabled", "isolation", "market_hours"}

_TRUE = {"1", "yes", "true", "on"}
_FALSE = {"0", "no", "false", "off"}
_ISOLATION_VALUES = {"own_ledger", "dedicated"}
_MARKET_HOURS_VALUES = {"24x7", "rth"}


def _as_bool(value: str, *, section: str, key: str) -> bool:
    normalized = str(value).strip().casefold()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    allowed = ", ".join(sorted(_TRUE | _FALSE))
    raise ValueError(
        f"instruments.conf [{section}]: {key!r} must be one of {allowed}; "
        f"got {value!r}")


def _required(d: dict, section: str, key: str) -> str:
    value = str(d.get(key) or "").strip()
    if not value:
        raise ValueError(f"instruments.conf [{section}]: missing required {key!r}")
    return value


# ---------------------------------------------------------------------------
# Lightweight derivation helpers (single source of truth for the fleet).
#
# These read instruments.conf with configparser only — no Instrument, no
# providers — so symbols.py and other import-sensitive modules can derive their
# lists from the registry. To add a Binance coin to the fleet, add ONE section
# here; symbols.py, the trailing stop and the tradeall allowlist all follow.
# ---------------------------------------------------------------------------
def _read_registry(path: Optional[str] = None):
    """Return [(section, {key: stripped_value})] in file order. Fail loud if absent."""
    path = path or DEFAULT_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"Instrument registry does not exist: {path}")
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cp.read(path)
    return [
        (section, {k: (v or "").strip() for k, v in cp.items(section)})
        for section in cp.sections()
    ]


def _enabled_binance(path: Optional[str] = None):
    """Yield (section, params) for enabled Binance instruments, in registry order."""
    for section, d in _read_registry(path):
        if d.get("provider", "").casefold() != "binance":
            continue
        if _as_bool(_required(d, section, "enabled"), section=section, key="enabled"):
            yield section, d


def binance_symbols(path: Optional[str] = None) -> list:
    """Enabled Binance venue symbols, in registry order — the single source for
    symbols.py. Fails loud: an empty/missing registry is a configuration error,
    never silently 'no coins'."""
    syms = [_required(d, section, "symbol") for section, d in _enabled_binance(path)]
    if not syms:
        raise ValueError(f"instruments.conf: no enabled Binance instruments in {path or DEFAULT_PATH}")
    return syms


def trail_pct_map(path: Optional[str] = None) -> Dict[str, float]:
    """{symbol: trail_pct} for enabled Binance instruments with trail.enabled truthy —
    the per-coin trailing stop configuration."""
    out: Dict[str, float] = {}
    for section, d in _enabled_binance(path):
        raw = d.get("trail.enabled")
        if raw and _as_bool(raw, section=section, key="trail.enabled"):
            out[_required(d, section, "symbol")] = float(_required(d, section, "trail.pct"))
    return out


def tradeall_trade_symbols(path: Optional[str] = None) -> set:
    """Set of enabled Binance symbols with tradeall.trade truthy — the tradeall
    order allowlist. A Binance coin absent here (or with tradeall.trade=no) is
    trend-tracked but never traded by tradeall."""
    out = set()
    for section, d in _enabled_binance(path):
        raw = d.get("tradeall.trade")
        if raw and _as_bool(raw, section=section, key="tradeall.trade"):
            out.add(_required(d, section, "symbol"))
    return out


def load_instruments(path: Optional[str] = None, api=None) -> Dict[str, "Instrument"]:
    """Build instruments keyed by configuration section name."""
    from instrument import Instrument  # lazy: keeps this module import-light (see top)
    path = path or DEFAULT_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"Instrument registry does not exist: {path}")
    out: Dict[str, Instrument] = {}
    seen_venue_symbols = set()
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cp.read(path)
    for section in cp.sections():
        d = dict(cp.items(section))
        provider = _required(d, section, "provider")
        symbol = _required(d, section, "symbol")
        base = _required(d, section, "base")
        quote = _required(d, section, "quote")
        enabled = _as_bool(_required(d, section, "enabled"), section=section, key="enabled")
        isolation = _required(d, section, "isolation").casefold()
        market_hours = _required(d, section, "market_hours").casefold()
        if isolation not in _ISOLATION_VALUES:
            raise ValueError(
                f"instruments.conf [{section}]: unsupported isolation {isolation!r}; "
                f"expected one of {sorted(_ISOLATION_VALUES)}")
        if market_hours not in _MARKET_HOURS_VALUES:
            raise ValueError(
                f"instruments.conf [{section}]: unsupported market_hours {market_hours!r}; "
                f"expected one of {sorted(_MARKET_HOURS_VALUES)}")
        venue_symbol = (provider.casefold(), symbol.casefold())
        if venue_symbol in seen_venue_symbols:
            raise ValueError(
                f"instruments.conf [{section}]: duplicate provider/symbol "
                f"{provider!r}/{symbol!r}")
        seen_venue_symbols.add(venue_symbol)
        params = {k: v for k, v in d.items() if k not in _CORE}
        out[section] = Instrument(
            name=section,
            symbol=symbol,
            provider=provider,
            base=base,
            quote=quote,
            enabled=enabled,
            isolation=isolation,
            market_hours=market_hours,
            params=params,
            api=api,
        )
    return out


def load_for(consumer: str, path: Optional[str] = None, api=None,
             only_enabled: bool = True) -> Dict[str, "Instrument"]:
    """Instruments relevant to one consumer (for example 'mt'): those with at least one
    key in its namespace, optionally only the enabled ones. Convenience helper for
    monitortrades/tradeall/rtrade."""
    pref = consumer + "."
    res = {}
    for name, inst in load_instruments(path, api).items():
        if only_enabled and not inst.enabled:
            continue
        if any(k.startswith(pref) for k in inst.params):
            res[name] = inst
    return res
