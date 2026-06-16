#!/usr/bin/env python3
# verify_instruments.py
"""Verifica fundatia (pasii 1-3): registry pe nume + Instrument + instruments.conf.

NU atinge flota. Confirma:
  1. instruments.conf se incarca; BTC/TAO/HYPE prezente, provider/symbol/base corecte.
  2. params mt.* OGLINDESC monitortrades.conf (behavior-preserving) — diff real.
  3. rutarea explicita pe venue: TAO->Binance, HYPE->Hyperliquid (provider_by_name).
  4. operatii generice prin Instrument == facada directa (pret, sold).
Exit 0 daca tot trece.
"""
import re
import sys

from market_api import api
from instrument import Instrument
from instruments_config import load_instruments

FAIL = []


def check(cond, label, detail=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(label)


def parse_monitortrades_conf(path="monitortrades.conf"):
    """Parser simplu pt formatul existent (key=val; SYMBOL = g / l / maxage)."""
    g = {}
    per = {}
    try:
        with open(path) as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if "=" not in line:
                    continue
                k, v = (x.strip() for x in line.split("=", 1))
                if "/" in v:                      # SYMBOL = gain / lost / maxage
                    parts = [p.strip() for p in v.split("/")]
                    if len(parts) == 3:
                        per[k] = parts
                else:
                    g[k] = v
    except FileNotFoundError:
        pass
    return g, per


print("==== 1. INCARCARE instruments.conf ====")
inst = load_instruments()
for name in ("BTC_BINANCE", "TAO_BINANCE", "HYPE_HL"):
    check(name in inst, f"sectiune {name} prezenta")
tao = inst.get("TAO_BINANCE")
btc = inst.get("BTC_BINANCE")
hype = inst.get("HYPE_HL")

if tao:
    check(tao.symbol == "TAOUSDC" and tao.base == "TAO", "TAO symbol/base",
          f"{tao.symbol}/{tao.base}")
if hype:
    check(hype.symbol == "HYPEUSDC" and hype.base == "HYPE", "HYPE symbol/base",
          f"{hype.symbol}/{hype.base}")
    check(hype.enabled is False, "HYPE enabled=no (gated)", str(hype.enabled))

print("\n==== 2. params mt.* == monitortrades.conf (behavior-preserving) ====")
g, per = parse_monitortrades_conf()
for symname, key in (("BTC_BINANCE", "BTCUSDC"), ("TAO_BINANCE", "TAOUSDC")):
    it = inst.get(symname)
    exp = per.get(key)
    if it and exp:
        got = [str(it.param("mt", "gain")), str(it.param("mt", "lost")),
               str(it.param("mt", "maxage_days"))]
        check([float(x) for x in got] == [float(x) for x in exp],
              f"{symname} gain/lost/maxage", f"conf={exp} instr={got}")
# globale -> mt.* pe TAO (oglindesc hard_tp_* + tp_reference)
if tao:
    check(str(tao.param("mt", "hardtp")) == str(g.get("hard_tp_pct")),
          "hardtp == hard_tp_pct", f"{tao.param('mt','hardtp')} vs {g.get('hard_tp_pct')}")
    check(str(tao.param("mt", "hardtp_fraction")) == str(g.get("hard_tp_fraction")),
          "hardtp_fraction", f"{tao.param('mt','hardtp_fraction')} vs {g.get('hard_tp_fraction')}")
    check(tao.param("mt", "ref") == g.get("tp_reference"),
          "ref == tp_reference", f"{tao.param('mt','ref')} vs {g.get('tp_reference')}")
    check(tao.param("mt", "gain", cast=float) == 9.2, "param() cast float", str(tao.param("mt", "gain", cast=float)))

print("\n==== 3. RUTARE explicita pe venue (provider_by_name) ====")
check(api.provider_by_name("binance") is not None, "provider_by_name('binance')")
check(api.provider_by_name("hyperliquid") is not None, "provider_by_name('hyperliquid')")
check(api.provider_by_name("nope") is None, "nume necunoscut -> None")
if tao:
    check(tao.provider_label == "Binance", "TAO -> Binance", tao.provider_label)
if hype:
    check(hype.provider_label == "Hyperliquid", "HYPE -> Hyperliquid", hype.provider_label)

print("\n==== 4. operatii generice == facada directa ====")
if tao:
    p_inst = tao.price()
    p_api = api.get_current_price("TAOUSDC")
    check(p_inst is not None and p_inst > 0 and p_inst == p_api,
          "TAO price() == facada", f"instr={p_inst} api={p_api}")
    f_inst = tao.free()
    f_prov = api.provider_by_name("binance").free_balance("TAO")
    check(f_inst is not None and f_inst == f_prov, "TAO free() == provider direct",
          f"instr={f_inst} prov={f_prov}")
if hype:
    p_h = hype.price()
    check(p_h is not None and p_h > 0, "HYPE price() > 0 (rutat la HL)", str(p_h))
    f_h = hype.free()
    f_hp = api.provider_by_name("hyperliquid").free_balance("HYPE")
    check(f_h is not None and f_h == f_hp, "HYPE free() == HL direct", f"instr={f_h} hl={f_hp}")

print("\n" + "=" * 56)
if FAIL:
    print(f"ESEC — {len(FAIL)} verificari picate: {FAIL}")
    sys.exit(1)
print("PASS — fundatia Instrument (pasii 1-3) e sanatoasa.")
sys.exit(0)
