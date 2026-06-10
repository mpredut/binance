#!/usr/bin/env python3
"""
hl_client.py — client Hyperliquid peste SDK-ul oficial.

Citiri (Info, fara semnatura): preturi, pozitii, ordine deschise, sold.
Tranzactionare (Exchange, semnatura EIP-712 cu agent wallet): plasare/anulare ordine.

Model: PERP long-only (HYPE perp e lichid). La levier mic (1x) e cvasi-spot,
lichidarea e foarte departe. "buy" = deschide/mareste long; "TP" = reduce long.

Necesita rularea cu python-ul din venv-ul cu SDK:
    /home/mariusp/binance/.venv/bin/python hl_bot.py
"""

from __future__ import annotations

import time

from common import log

try:
    import eth_account
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    _SDK_OK = True
except Exception as _e:  # noqa: BLE001
    _SDK_OK = False
    _SDK_ERR = str(_e)


class HLError(Exception):
    pass


def _round_px(px: float, sz_decimals: int, is_perp: bool = True) -> float:
    """Pretul HL: max 5 cifre semnificative si max (6/8 - szDecimals) zecimale."""
    if px <= 0:
        return px
    max_dec = (6 if is_perp else 8) - sz_decimals
    px = float(f"{px:.5g}")           # 5 cifre semnificative
    return round(px, max(max_dec, 0))


class HLClient:
    def __init__(self, secret_key: str | None = None, account_address: str | None = None,
                 mainnet: bool = True):
        if not _SDK_OK:
            raise HLError(f"SDK Hyperliquid indisponibil: {_SDK_ERR} "
                          f"(ruleaza cu python-ul din .venv)")
        self.base = constants.MAINNET_API_URL if mainnet else constants.TESTNET_API_URL
        self.info = Info(self.base, skip_ws=True)
        self.address = account_address
        self.exchange = None
        if secret_key:
            wallet = eth_account.Account.from_key(secret_key)
            self.address = account_address or wallet.address
            self.exchange = Exchange(wallet, self.base, account_address=self.address)
        self._meta_cache: dict[str, dict] = {}

    # ----- meta / preturi ------------------------------------------------------
    def _meta(self) -> dict:
        if not self._meta_cache:
            for a in self.info.meta().get("universe", []):
                self._meta_cache[a["name"]] = a
        return self._meta_cache

    def sz_decimals(self, coin: str) -> int:
        return int(self._meta().get(coin, {}).get("szDecimals", 2))

    def max_leverage(self, coin: str) -> int:
        return int(self._meta().get(coin, {}).get("maxLeverage", 1))

    def coin_listed(self, coin: str) -> bool:
        return coin in self._meta()

    def mid(self, coin: str) -> float | None:
        try:
            mids = self.info.all_mids()
            v = mids.get(coin)
            return float(v) if v is not None else None
        except Exception as e:  # noqa: BLE001
            log(f"  ! mid({coin}) esuat: {e}")
            return None

    # ----- cont (read-only, doar adresa) --------------------------------------
    def _user_state(self) -> dict:
        if not self.address:
            raise HLError("HL_ACCOUNT_ADDRESS lipsa")
        return self.info.user_state(self.address)

    def position_strict(self, coin: str) -> tuple[float, float]:
        """Ca position(), dar RIDICA exceptia la eroare API — pt cod care trebuie
        sa distinga 'nu am pozitie' (0) de 'nu stiu' (ex. delta-neutral, unde un
        0 fals ar duce la deschiderea unui picior dublu)."""
        for ap in self._user_state().get("assetPositions", []):
            p = ap.get("position", {})
            if p.get("coin") == coin:
                return float(p.get("szi") or 0), float(p.get("entryPx") or 0)
        return 0.0, 0.0

    def position(self, coin: str) -> tuple[float, float]:
        """(szi, entryPx) pentru coin. szi>0 = long. (0,0) daca nu exista pozitie
        SAU la eroare API (logata)."""
        try:
            return self.position_strict(coin)
        except HLError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"  ! position({coin}) esuat: {e}")
        return 0.0, 0.0

    def withdrawable(self) -> float:
        try:
            return float(self._user_state().get("withdrawable") or 0)
        except Exception:  # noqa: BLE001
            return 0.0

    def open_orders(self, coin: str | None = None) -> list[dict]:
        if not self.address:
            return []
        try:
            oo = self.info.open_orders(self.address)
        except Exception as e:  # noqa: BLE001
            log(f"  ! open_orders esuat: {e}")
            return []
        return [o for o in oo if coin is None or o.get("coin") == coin]

    # ----- SPOT + funding (citiri) --------------------------------------------
    def resolve_spot_pair(self, token: str) -> str | None:
        """Gaseste automat perechea spot TOKEN/USDC (@index) din spotMeta —
        generic pt orice token (HYPE -> @107, USOL -> @156, PURR -> PURR/USDC)."""
        try:
            m = self.info.spot_meta()
            tokens = {t.get("name"): t.get("index") for t in m.get("tokens", [])}
            ti, usdc = tokens.get(token), tokens.get("USDC")
            if ti is None or usdc is None:
                return None
            for u in m.get("universe", []):
                if u.get("tokens") == [ti, usdc]:
                    return u.get("name")
        except Exception as e:  # noqa: BLE001
            log(f"  ! resolve_spot_pair({token}) esuat: {e}")
        return None

    def spot_mid(self, pair: str) -> float | None:
        """Pret spot pentru perechea @index (ex @107 = HYPE/USDC)."""
        try:
            v = self.info.all_mids().get(pair)
            return float(v) if v is not None else None
        except Exception as e:  # noqa: BLE001
            log(f"  ! spot_mid({pair}) esuat: {e}")
            return None

    def spot_balance_strict(self, token: str) -> float:
        """Ca spot_balance(), dar RIDICA exceptia la eroare API (vezi position_strict)."""
        if not self.address:
            raise HLError("HL_ACCOUNT_ADDRESS lipsa")
        for b in self.info.spot_user_state(self.address).get("balances", []):
            if b.get("coin") == token:
                return float(b.get("total") or 0)
        return 0.0

    def spot_balance(self, token: str) -> float:
        """Cantitatea detinuta din token-ul spot (ex 'HYPE', 'USDC').
        0.0 daca nu exista SAU la eroare API (logata)."""
        try:
            return self.spot_balance_strict(token)
        except Exception as e:  # noqa: BLE001
            log(f"  ! spot_balance({token}) esuat: {e}")
        return 0.0

    def funding_rate(self, coin: str) -> float | None:
        """Rata de funding curenta (pe ora) a perp-ului. Pozitiv = long platesc short."""
        try:
            meta, ctxs = self.info.meta_and_asset_ctxs()
            for i, a in enumerate(meta["universe"]):
                if a["name"] == coin:
                    return float(ctxs[i].get("funding") or 0)
        except Exception as e:  # noqa: BLE001
            log(f"  ! funding_rate({coin}) esuat: {e}")
        return None

    def position_full(self, coin: str) -> dict | None:
        """Pozitia perp completa: szi, entryPx, liquidationPx, unrealizedPnl, marginUsed..."""
        try:
            for ap in self._user_state().get("assetPositions", []):
                p = ap.get("position", {})
                if p.get("coin") == coin:
                    return p
        except Exception as e:  # noqa: BLE001
            log(f"  ! position_full esuat: {e}")
        return None

    def margin_summary(self) -> dict:
        """Valoarea contului perp + margine folosita + retragibil."""
        try:
            st = self._user_state()
            ms = st.get("marginSummary", {})
            return {"accountValue": float(ms.get("accountValue") or 0),
                    "totalMarginUsed": float(ms.get("totalMarginUsed") or 0),
                    "withdrawable": float(st.get("withdrawable") or 0)}
        except Exception:  # noqa: BLE001
            return {}

    def funding_history(self, start_ms: int) -> list[dict]:
        """Istoricul platilor de funding (real incasat/platit) de la start_ms incoace."""
        if not self.address:
            return []
        try:
            return self.info.user_funding_history(self.address, start_ms)
        except Exception as e:  # noqa: BLE001
            log(f"  ! funding_history esuat: {e}")
            return []

    def candles(self, coin: str, interval: str = "1h", lookback_hours: int = 60) -> list[dict]:
        """Lumanari OHLCV (pentru indicatori de trend)."""
        end = int(time.time() * 1000)
        start = end - lookback_hours * 3600 * 1000
        try:
            return self.info.candles_snapshot(coin, interval, start, end)
        except Exception as e:  # noqa: BLE001
            log(f"  ! candles({coin}) esuat: {e}")
            return []

    def spot_order(self, pair: str, is_buy: bool, sz: float, px: float,
                   sz_decimals: int = 2) -> tuple[bool, int | None, str]:
        """Ordin LIMIT pe spot. pair = numele @index (ex @107)."""
        if not self.exchange:
            raise HLError("Fara agent wallet (HL_SECRET_KEY)")
        sz = round(sz, sz_decimals)
        px = _round_px(px, sz_decimals, is_perp=False)
        try:
            res = self.exchange.order(pair, is_buy, sz, px, {"limit": {"tif": "Gtc"}})
        except Exception as e:  # noqa: BLE001
            return False, None, str(e)
        if res.get("status") != "ok":
            return False, None, str(res)
        st = res["response"]["data"]["statuses"][0]
        if "resting" in st:
            return True, st["resting"]["oid"], "resting"
        if "filled" in st:
            return True, st["filled"].get("oid"), f"filled {st['filled'].get('totalSz')}"
        if "error" in st:
            return False, None, st["error"]
        return True, None, str(st)

    # ----- tranzactionare (semnat) --------------------------------------------
    def set_leverage(self, coin: str, leverage: int) -> None:
        if not self.exchange:
            return
        try:
            self.exchange.update_leverage(leverage, coin, is_cross=True)
            log(f"  [HL] levier {coin} setat la {leverage}x")
        except Exception as e:  # noqa: BLE001
            log(f"  ! set_leverage esuat: {e}")

    def place_limit(self, coin: str, is_buy: bool, sz: float, px: float,
                    reduce_only: bool = False) -> tuple[bool, int | None, str]:
        """Plaseaza ordin LIMIT GTC. Returneaza (ok, oid, mesaj)."""
        if not self.exchange:
            raise HLError("Fara agent wallet (HL_SECRET_KEY) — nu pot plasa ordine")
        sz = round(sz, self.sz_decimals(coin))
        px = _round_px(px, self.sz_decimals(coin))
        try:
            res = self.exchange.order(coin, is_buy, sz, px,
                                      {"limit": {"tif": "Gtc"}}, reduce_only=reduce_only)
        except Exception as e:  # noqa: BLE001
            return False, None, str(e)
        if res.get("status") != "ok":
            return False, None, str(res)
        st = res["response"]["data"]["statuses"][0]
        if "resting" in st:
            return True, st["resting"]["oid"], "resting"
        if "filled" in st:
            return True, st["filled"].get("oid"), f"filled {st['filled'].get('totalSz')} @ {st['filled'].get('avgPx')}"
        if "error" in st:
            return False, None, st["error"]
        return True, None, str(st)

    def cancel(self, coin: str, oid: int) -> bool:
        if not self.exchange:
            return False
        try:
            res = self.exchange.cancel(coin, oid)
            return res.get("status") == "ok"
        except Exception as e:  # noqa: BLE001
            log(f"  ! cancel esuat: {e}")
            return False
