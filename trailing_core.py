#!/usr/bin/env python3
"""
trailing_core.py — nucleul provider-agnostic al trailing-stop-ului cu re-buy.

De ce exista: binance_api/trailing_stop.py si kraken/trailing_stop.py aveau ACEEASI
masina de stari (urmareste varful -> vinde la -trail% de la varf -> re-buy pe reculul
de la minimul de dupa vanzare), copiata aproape linie-cu-linie. Aici sta logica de
CONTROL (identica); fiecare provider ramane un ADAPTOR subtire care doar isi executa
apelurile lui de API (pret, balanta, sell, buy, trend, notify) + propriile log-uri.

Comportament IDENTIC cu inainte de extragere — garantat de tests/test_trailing_stop.py
si kraken/test_trailing_kraken.py, care raman verzi neschimbate (testeaza clasele-adaptor
TrailingStop / KrakenTrailing prin aceeasi masina de stari, acum centralizata aici).

Schema de stare (per cheie, persistata de core — NESCHIMBATA fata de versiunea veche,
ca starile deja salvate ale demonilor care ruleaza sa se incarce mai departe):
  { "<key>": { "peak": float, "rebuy": {"qty","sell_price","low"}  (optional) } }

Contractul ADAPTORULUI (duck-typing — vezi cele doua clase-adaptor):
  assets()        -> iterabil de (key, asset, pair, trail_pct)
  begin_tick()    -> bool          # False = sari tick-ul (ex. balante indisponibile)
  free_qty(asset) -> float         # cantitatea LIBERA de protejat
  price(pair)     -> float | None
  trend(pair)     -> float         # >0 sus, <0 jos, 0 neutru/necunoscut (filtre = no-op la 0)
  execute_sell(key, asset, pair, qty, price, peak, trail) -> bool  # plaseaza+logheaza+notifica; True=ok
  execute_rebuy(key, asset, pair, qty, price, rb)         -> bool  # idem; False -> pastreaza rebuy, reincearca
  log_dry_sell / log_dry_rebuy / log_hold / log_skip_rebuy_trend /
  log_skip_sell_trend / log_item_error / log_tick_error            # doar log (wording specific provider)
"""

from __future__ import annotations

import json
import os


def should_sell(current: float, peak: float, trail_pct: float) -> bool:
    """True daca pretul a cazut >= trail% de la varf."""
    return peak > 0 and trail_pct > 0 and current <= peak * (1 - trail_pct / 100.0)


class TrailingCore:
    def __init__(self, adapter, *, log, enabled, state_file, min_notional,
                 rebuy_enabled, rebuy_bounce_pct, rebuy_skip_if_trend_down,
                 sell_skip_if_trend_up, sell_fraction=1.0, item_isolation=True):
        self.a = adapter
        self.log = log
        self.enabled = enabled
        self.state_file = state_file
        self.min_notional = min_notional
        self.rebuy_enabled = rebuy_enabled
        self.rebuy_bounce_pct = rebuy_bounce_pct
        self.rebuy_skip_if_trend_down = rebuy_skip_if_trend_down
        self.sell_skip_if_trend_up = sell_skip_if_trend_up
        self.sell_fraction = sell_fraction
        # item_isolation=True (Binance): try per-moneda + save mereu dupa bucla (o moneda
        # picata nu opreste restul). False (Kraken): try pe tot tick-ul; eroare -> log + fara
        # save (reincearca data viitoare). Pastreaza exact structura de erori a fiecaruia.
        self.item_isolation = item_isolation

    # -- stare (varful per cheie) ---------------------------------------------
    def load(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, state: dict) -> None:
        try:
            d = os.path.dirname(self.state_file)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.state_file)
        except OSError as e:
            self.log(f"  ! [TRAIL] nu pot salva starea: {e}")

    # -- re-buy dupa crash sell -----------------------------------------------
    def _handle_rebuy(self, key, asset, pair, st: dict, price: float) -> None:
        """Recumparare dupa stop-loss de crash: cand pretul revine rebuy_bounce_pct% de la
        minimul de dupa vanzare (confirma ca s-a oprit caderea -> nu prinde cutitul)."""
        rb = st.get("rebuy")
        if not rb:
            return
        rb["low"] = min(rb.get("low", price), price)          # urmareste fundul de dupa vanzare
        if price < rb["low"] * (1 + self.rebuy_bounce_pct / 100.0):
            return                                            # reculul inca neconfirmat -> asteapta
        if self.rebuy_skip_if_trend_down and self.a.trend(pair) < 0:
            self.a.log_skip_rebuy_trend(asset)
            return
        qty = round(float(rb.get("qty", 0)), 8)               # 1 transa = qty intreg vandut
        if qty <= 0:
            st.pop("rebuy", None)
            return
        if self.enabled and qty * price >= self.min_notional:
            if not self.a.execute_rebuy(key, asset, pair, qty, price, rb):
                return                                        # esuat -> pastreaza rebuy, reincearca data viitoare
        else:
            self.a.log_dry_rebuy(key, asset, pair, qty, price, rb)
        st.pop("rebuy", None)                                 # 1 transa -> gata

    # -- un activ -------------------------------------------------------------
    def _process(self, key, asset, pair, trail, state) -> None:
        free = self.a.free_qty(asset)
        price = self.a.price(pair)
        if not price or price <= 0:
            return
        st = state.setdefault(key, {"peak": price})
        if self.rebuy_enabled and st.get("rebuy"):            # re-buy pending INAINTE de check-ul de notional (free~0 dupa vanzare)
            self._handle_rebuy(key, asset, pair, st, price)
        if free * price < self.min_notional:
            return                                            # nimic de protejat
        if price > st["peak"]:
            st["peak"] = price                                # varf nou -> urca trailing-ul
        stop_at = st["peak"] * (1 - trail / 100.0)
        if should_sell(price, st["peak"], trail):
            if self.sell_skip_if_trend_up and self.a.trend(pair) > 0:
                self.a.log_skip_sell_trend(key, asset, pair, trail)
                return
            sell_qty = round(free * self.sell_fraction, 8)
            if self.enabled and sell_qty * price >= self.min_notional:
                if self.a.execute_sell(key, asset, pair, sell_qty, price, st["peak"], trail):
                    st["peak"] = price                        # re-armeaza de la pretul curent
                    if self.rebuy_enabled:                    # armeaza re-buy: recumpara cand pretul revine de la minim
                        st["rebuy"] = {"qty": sell_qty, "sell_price": price, "low": price}
            else:
                self.a.log_dry_sell(key, asset, pair, sell_qty, price, st["peak"], trail)
        else:
            self.a.log_hold(key, asset, pair, price, st["peak"], stop_at, trail, free)

    # -- un pas ---------------------------------------------------------------
    def check_once(self) -> None:
        if not self.a.begin_tick():
            return
        if self.item_isolation:                               # Binance: izoleaza fiecare moneda, salveaza mereu
            state = self.load()
            for key, asset, pair, trail in self.a.assets():
                try:
                    self._process(key, asset, pair, trail, state)
                except Exception as e:  # noqa: BLE001 — o moneda nu opreste restul
                    self.a.log_item_error(key, e)
            self.save(state)
        else:                                                 # Kraken: tot tick-ul intr-un try; eroare -> reincearca (fara save)
            try:
                state = self.load()
                for key, asset, pair, trail in self.a.assets():
                    self._process(key, asset, pair, trail, state)
                self.save(state)
            except Exception as e:  # noqa: BLE001 — rezilienta: net picat -> reincearca
                self.a.log_tick_error(e)
