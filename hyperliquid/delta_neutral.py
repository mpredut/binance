#!/usr/bin/env python3
"""
delta_neutral.py — strategie MARKET-NEUTRAL pe Hyperliquid (funding farming).

Idee: ții LONG pe SPOT + SHORT pe PERP, mărimi egale -> delta pe pret ~ 0.
  * Daca HYPE urca: spot castiga = perp pierde (se anuleaza).
  * Daca HYPE scade: spot pierde = perp castiga (se anuleaza).
  * Castigul real = FUNDING-ul incasat fiind short pe perp (cand funding > 0)
    minus fee-uri si eventuala drift de baza (basis).

Straturi:
  legs()      -> citeste cele doua picioare (spot_qty, perp_szi) + preturi + funding
  decide()    -> pe baza funding-ului: deschide / tine / inchide
  execute     -> _open / _rebalance / _close (face diff catre tinta)

NU e „bani gratis": funding-ul se poate INVERSA (devii platitor), ai fee pe
4 ordine/ciclu (2 deschidere + 2 inchidere) si cost de rebalansare. Castig mic, constant.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from common import log, now_str, float_env
from notify import notify
from hl_client import HLClient, HLError

_HERE = os.path.dirname(os.path.abspath(__file__))
HL_FEE_PCT = float_env("HL_FEE_PCT") or 0.035
STATE = os.path.join(_HERE, ".state_dn.json")


@dataclass
class DNParams:
    coin: str            # perp (ex HYPE)
    spot_pair: str       # @index spot (ex @107)
    spot_token: str      # tokenul spot (ex HYPE)
    notional: float      # USDC per picior
    min_funding_hr: float # funding/ora minim ca sa stai in pozitie (ex 0.0)
    rebalance_pct: float # toleranta delta (% din marime) inainte de rebalansare
    check_minutes: float
    sz_decimals: int
    liq_alert_pct: float # alerta cand pretul e la acest % de pretul de lichidare al short-ului
    auto_protect: bool   # True = reduce automat pozitia cand se apropie lichidarea
    reduce_pct: float    # cu cat % reduce ambele picioare la fiecare interventie
    perp_leverage: int   # levier pe short (mic = margine multa = lichidare departe)

    @classmethod
    def from_env(cls, client: HLClient | None = None) -> "DNParams":
        coin = os.environ.get("HL_COIN", "HYPE").strip()
        szd = 2
        if client is not None:
            try: szd = client.sz_decimals(coin)
            except HLError: pass
        return cls(
            coin        = coin,
            spot_pair   = os.environ.get("HL_SPOT_PAIR", "@107").strip(),
            spot_token  = os.environ.get("HL_SPOT_TOKEN", coin).strip(),
            notional    = float_env("DN_NOTIONAL") or 100.0,
            min_funding_hr = (float_env("DN_MIN_FUNDING_HR_PCT") or 0.0) / 100.0,
            rebalance_pct  = float_env("DN_REBALANCE_PCT") or 5.0,
            check_minutes  = float_env("DN_CHECK_MINUTES") or 5.0,
            sz_decimals = szd,
            liq_alert_pct = float_env("DN_LIQ_ALERT_PCT") or 20.0,
            auto_protect  = os.environ.get("DN_AUTO_PROTECT", "true").strip().lower() == "true",
            reduce_pct    = float_env("DN_REDUCE_PCT") or 25.0,
            perp_leverage = int(float_env("DN_PERP_LEVERAGE") or 1),
        )


def _new_state() -> dict:
    return {"status": "flat", "target_sz": 0.0, "fees_paid": 0.0,
            "funding_accrued": 0.0, "opened_at": None, "liq_alerted": False,
            "spot_qty": 0.0, "perp_szi": 0.0}   # spot_qty/perp_szi folosite doar in PAPER


class DeltaNeutral:
    def __init__(self, client: HLClient, params: DNParams, dry_run: bool = True, desktop: bool = False):
        self.client = client
        self.p = params
        self.dry_run = dry_run
        self.desktop = desktop
        self.s = self._load()

    def _load(self) -> dict:
        if os.path.exists(STATE):
            try:
                with open(STATE) as f:
                    m = _new_state(); m.update(json.load(f)); return m
            except (OSError, ValueError):
                pass
        return _new_state()

    def _save(self):
        try:
            with open(STATE, "w") as f: json.dump(self.s, f, indent=2)
        except OSError as e:
            log(f"  ! [DN] nu pot salva: {e}")

    # -- stratul de citire: cele doua picioare ---------------------------------
    def legs(self) -> dict:
        spot_px = self.client.spot_mid(self.p.spot_pair)
        perp_px = self.client.mid(self.p.coin)
        funding = self.client.funding_rate(self.p.coin)
        if self.dry_run:
            spot_qty = self.s["spot_qty"]; perp_szi = self.s["perp_szi"]
        else:
            spot_qty = self.client.spot_balance(self.p.spot_token)
            perp_szi, _ = self.client.position(self.p.coin)
        return {"spot_px": spot_px, "perp_px": perp_px, "funding": funding,
                "spot_qty": spot_qty, "perp_szi": perp_szi}

    def _round(self, sz: float) -> float:
        return round(sz, self.p.sz_decimals)

    # -- executie ---------------------------------------------------------------
    def _buy_spot(self, sz: float, px: float):
        sz = self._round(sz)
        if sz <= 0: return
        if self.dry_run:
            self.s["spot_qty"] += sz; log(f"  [DN] [PAPER] BUY SPOT {sz} {self.p.spot_token} @ {px:.4f}")
        else:
            ok, oid, msg = self.client.spot_order(self.p.spot_pair, True, sz, px * 1.001, self.p.sz_decimals)
            log(f"  [DN] BUY SPOT {sz} @ ~{px:.4f} -> ok={ok} {msg}")
        self.s["fees_paid"] += (HL_FEE_PCT/100) * sz * px

    def _sell_spot(self, sz: float, px: float):
        sz = self._round(sz)
        if sz <= 0: return
        if self.dry_run:
            self.s["spot_qty"] -= sz; log(f"  [DN] [PAPER] SELL SPOT {sz} @ {px:.4f}")
        else:
            ok, oid, msg = self.client.spot_order(self.p.spot_pair, False, sz, px * 0.999, self.p.sz_decimals)
            log(f"  [DN] SELL SPOT {sz} @ ~{px:.4f} -> ok={ok} {msg}")
        self.s["fees_paid"] += (HL_FEE_PCT/100) * sz * px

    def _short_perp(self, sz: float, px: float):
        sz = self._round(sz)
        if sz <= 0: return
        if self.dry_run:
            self.s["perp_szi"] -= sz; log(f"  [DN] [PAPER] SHORT PERP {sz} {self.p.coin} @ {px:.4f}")
        else:
            ok, oid, msg = self.client.place_limit(self.p.coin, False, sz, px * 0.999, reduce_only=False)
            log(f"  [DN] SHORT PERP {sz} @ ~{px:.4f} -> ok={ok} {msg}")
        self.s["fees_paid"] += (HL_FEE_PCT/100) * sz * px

    def _cover_perp(self, sz: float, px: float):
        sz = self._round(sz)
        if sz <= 0: return
        if self.dry_run:
            self.s["perp_szi"] += sz; log(f"  [DN] [PAPER] COVER PERP {sz} @ {px:.4f}")
        else:
            ok, oid, msg = self.client.place_limit(self.p.coin, True, sz, px * 1.001, reduce_only=True)
            log(f"  [DN] COVER PERP {sz} @ ~{px:.4f} -> ok={ok} {msg}")
        self.s["fees_paid"] += (HL_FEE_PCT/100) * sz * px

    # -- actiuni de nivel inalt -------------------------------------------------
    def _open(self, L: dict):
        sz = self._round(self.p.notional / L["perp_px"])
        # PREVENTIV: levier mic pe short -> margine multa -> lichidare foarte departe
        if not self.dry_run and self.client.exchange:
            self.client.set_leverage(self.p.coin, self.p.perp_leverage)
        log(f"  [DN] >>> DESCHID delta-neutral: long {sz} SPOT + short {sz} PERP {self.p.coin} "
            f"(~{self.p.notional} USDC/picior, levier {self.p.perp_leverage}x)")
        self._buy_spot(sz, L["spot_px"])
        self._short_perp(sz, L["perp_px"])
        self.s["status"] = "open"; self.s["target_sz"] = sz; self.s["opened_at"] = now_str()
        notify(title=f"Delta-neutral {self.p.coin} DESCHIS",
               body=f"long {sz} spot + short {sz} perp\nfunding {L['funding']*100:.4f}%/ora\n{now_str()}",
               source="dn", desktop=self.desktop)

    def _close(self, L: dict, reason: str):
        sz_spot = self._round(L["spot_qty"]); sz_perp = self._round(abs(L["perp_szi"]))
        log(f"  [DN] <<< INCHID delta-neutral ({reason}): vand {sz_spot} spot + acopar {sz_perp} perp")
        if sz_spot > 0: self._sell_spot(sz_spot, L["spot_px"])
        if sz_perp > 0: self._cover_perp(sz_perp, L["perp_px"])
        notify(title=f"Delta-neutral {self.p.coin} INCHIS ({reason})",
               body=f"funding incasat ~{self.s['funding_accrued']:+.4f}  fee ~{self.s['fees_paid']:.4f}\n{now_str()}",
               source="dn", desktop=self.desktop)
        keep_fund = self.s["funding_accrued"]; keep_fee = self.s["fees_paid"]
        self.s = _new_state(); self.s["funding_accrued"] = keep_fund; self.s["fees_paid"] = keep_fee

    def _rebalance(self, L: dict):
        """Aduce ambele picioare la target_sz (corecteaza fill-uri partiale / drift)."""
        tgt = self.s["target_sz"]; tol = tgt * self.p.rebalance_pct/100
        if abs(L["spot_qty"] - tgt) > tol:
            d = tgt - L["spot_qty"]
            (self._buy_spot if d > 0 else self._sell_spot)(abs(d), L["spot_px"])
            log(f"  [DN] rebalans SPOT {d:+.4f} (target {tgt})")
        short = abs(L["perp_szi"])
        if abs(short - tgt) > tol:
            d = tgt - short
            (self._short_perp if d > 0 else self._cover_perp)(abs(d), L["perp_px"])
            log(f"  [DN] rebalans PERP {d:+.4f} (target {tgt})")

    # -- monitorizare + protectie lichidare (doar short-ul perp poate fi lichidat) --
    def _check_liq(self, L: dict) -> bool:
        """Returneaza True daca a redus pozitia (ca sa sarim peste rebalans in acel tick)."""
        pos = self.client.position_full(self.p.coin)
        if not pos:
            return False
        try:
            liq = float(pos.get("liquidationPx") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        if liq <= 0:
            return False
        perp_px = L["perp_px"]
        dist_pct = (liq - perp_px) / perp_px * 100   # short: lichidare DEASUPRA pretului
        if 0 < dist_pct <= self.p.liq_alert_pct:
            if not self.s.get("liq_alerted"):
                self.s["liq_alerted"] = True
                log(f"  ⚠ [DN] SHORT aproape de LICHIDARE! pret={perp_px:.4f} liq={liq:.4f} ({dist_pct:.1f}% distanta)")
                notify(title=f"⚠ {self.p.coin}: short aproape de LICHIDARE!",
                       body=(f"Pret {perp_px:.4f}, lichidare la {liq:.4f} (doar {dist_pct:.1f}% distanta).\n{now_str()}"),
                       source="dn", desktop=self.desktop)
            # PREVENTIV: reduce automat ambele picioare -> scade short-ul -> lichidarea se departeaza
            if self.p.auto_protect:
                cut = self._round(abs(L["perp_szi"]) * self.p.reduce_pct / 100)
                if cut > 0:
                    log(f"  🛡 [DN] AUTO-PROTECT: reduc ambele picioare cu {cut} (de-risk, raman neutru)")
                    self._cover_perp(cut, perp_px)
                    self._sell_spot(cut, L["spot_px"])
                    self.s["target_sz"] = max(0.0, self.s["target_sz"] - cut)
                    notify(title=f"🛡 {self.p.coin}: am redus preventiv pozitia",
                           body=(f"Aproape de lichidare -> am redus {cut} pe ambele picioare.\n"
                                 f"Pozitia e mai mica si mai sigura, tot neutra.\n{now_str()}"),
                           source="dn", desktop=self.desktop)
                    return True
        elif dist_pct > self.p.liq_alert_pct * 1.5:
            self.s["liq_alerted"] = False   # revenit la siguranta -> re-armeaza alerta
        return False

    # -- bucla ------------------------------------------------------------------
    def run(self):
        log("  === DELTA-NEUTRAL PORNIT ===")
        log(f"      coin={self.p.coin} spot={self.p.spot_pair}  notional={self.p.notional} USDC/picior  {'[PAPER]' if self.dry_run else '⚠ REAL'}")
        log(f"      min funding ca sa stau: {self.p.min_funding_hr*100:.4f}%/ora   rebalans la {self.p.rebalance_pct}% delta")
        try:
            while True:
                L = self.legs()
                if L["spot_px"] is None or L["perp_px"] is None or L["funding"] is None:
                    log("  [DN] date indisponibile — reincerc"); time.sleep(self.p.check_minutes*60); continue
                self.tick(L)
                self._save()
                time.sleep(self.p.check_minutes*60)
        except KeyboardInterrupt:
            log("  [DN] oprit manual."); self._save()

    def tick(self, L: dict) -> None:
        """Un pas de decizie (extras ca sa fie testabil): deschide / tine / inchide / rebalanseaza."""
        # RECONCILIERE: daca exista deja o pozitie pe cont (restart / state sters), o ADOPTAM
        # -> nu deschidem din nou (anti-dublare).
        if not self.dry_run and self.s["status"] == "flat":
            sq, pq = abs(L["spot_qty"]), abs(L["perp_szi"])
            if sq > 1e-6 and pq > 1e-6:
                self.s["status"] = "open"
                self.s["target_sz"] = round((sq + pq) / 2, 6)
                log(f"  [DN] adopt pozitie existenta: spot {L['spot_qty']} / perp {L['perp_szi']} "
                    f"-> status=open, target={self.s['target_sz']}")
        fhr = L["funding"]
        delta = L["spot_qty"] + L["perp_szi"]       # ~0 cand e hedge-uit
        basis = (L["perp_px"] - L["spot_px"]) / L["spot_px"] * 100

        if self.s["status"] == "flat":
            if fhr >= self.p.min_funding_hr:
                self._open(L)
            else:
                log(f"  [DN] funding {fhr*100:.4f}%/ora < prag — astept (flat)")
        else:
            self.s["funding_accrued"] += fhr * abs(L["perp_szi"]) * L["perp_px"] * (self.p.check_minutes/60)
            reduced = self._check_liq(L)            # protectie: alerta + reduce automat
            if fhr < self.p.min_funding_hr:
                self._close(L, f"funding {fhr*100:.4f}%/ora sub prag")
            elif not reduced:                       # daca am redus, sar peste rebalans (date proaspete la urmatorul tick)
                self._rebalance(L)

        log(f"  [DN] funding={fhr*100:+.4f}%/ora (~{fhr*24*365*100:.1f}%/an)  delta={delta:+.4f}  "
            f"basis={basis:+.3f}%  status={self.s['status']}  "
            f"funding_acumulat~{self.s['funding_accrued']:+.4f}  fee~{self.s['fees_paid']:.4f} USDC")
