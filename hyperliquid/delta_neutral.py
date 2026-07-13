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

import fcntl
import json
import os
import time
from dataclasses import dataclass

from common import log, now_str, float_env
from notify import notify
from hl_client import HLClient, HLError

_HERE = os.path.dirname(os.path.abspath(__file__))
HL_FEE_PCT = float_env("HL_FEE_PCT") or 0.035
MIN_ORDER_USD = 10.5          # Hyperliquid respinge ordine sub ~$10 — nu le mai trimitem
_OLD_STATE = os.path.join(_HERE, ".state_dn.json")   # numele vechi (o singura moneda)


def state_path_for(coin: str) -> str:
    """Stare per-moneda — poti rula DN pe mai multe monede in paralel."""
    safe = "".join(c if c.isalnum() else "_" for c in coin)
    return os.path.join(_HERE, f".state_dn_{safe}.json")


@dataclass
class DNParams:
    coin: str            # perp (ex HYPE)
    spot_pair: str       # @index spot (ex @107)
    spot_token: str      # tokenul spot (ex HYPE)
    notional: float      # USDC per picior
    entry_funding_hr: float  # deschide cand funding MEDIU >= asta
    exit_funding_hr: float   # inchide cand funding MEDIU < asta (mai negativ -> histerezis, nu churn)
    funding_window_h: float  # peste cate ore mediezi funding-ul (anti-zgomot, ignora citiri izolate)
    min_hold_h: float        # nu inchide mai devreme de atat (lasa timpul sa lucreze pt funding)
    rebalance_pct: float # toleranta delta (% din marime) inainte de rebalansare
    check_minutes: float
    sz_decimals: int
    liq_alert_pct: float # alerta cand pretul e la acest % de pretul de lichidare al short-ului
    auto_protect: bool   # True = reduce automat pozitia cand se apropie lichidarea
    reduce_pct: float    # cu cat % reduce ambele picioare la fiecare interventie
    perp_leverage: int   # levier pe short (mic = margine multa = lichidare departe)
    allow_scale_up: bool # True = creste pozitia VIE pana la 'notional' (cu garda de colateral)

    @classmethod
    def from_env(cls, client: HLClient | None = None) -> "DNParams":
        coin = os.environ.get("HL_COIN", "HYPE").strip()
        szd = 2
        if client is not None:
            try: szd = client.sz_decimals(coin)
            except HLError: pass
        spot_token = os.environ.get("HL_SPOT_TOKEN", coin).strip()
        spot_pair = os.environ.get("HL_SPOT_PAIR", "").strip()
        if not spot_pair and client is not None:       # gol = rezolva automat din spotMeta
            spot_pair = client.resolve_spot_pair(spot_token) or ""
            if spot_pair:
                log(f"  [DN] pereche spot rezolvata automat: {spot_token} -> {spot_pair}")
        if not spot_pair:
            spot_pair = "@107"                          # fallback istoric (HYPE/USDC)
        return cls(
            coin        = coin,
            spot_pair   = spot_pair,
            spot_token  = spot_token,
            notional    = float_env("DN_NOTIONAL") or 100.0,
            entry_funding_hr = (float_env("DN_ENTRY_FUNDING_HR_PCT") if float_env("DN_ENTRY_FUNDING_HR_PCT") is not None else 0.0) / 100.0,
            exit_funding_hr  = (float_env("DN_EXIT_FUNDING_HR_PCT") if float_env("DN_EXIT_FUNDING_HR_PCT") is not None else -0.005) / 100.0,
            funding_window_h = float_env("DN_FUNDING_WINDOW_H") or 4.0,
            min_hold_h       = float_env("DN_MIN_HOLD_H") or 6.0,
            rebalance_pct  = float_env("DN_REBALANCE_PCT") or 5.0,
            check_minutes  = float_env("DN_CHECK_MINUTES") or 5.0,
            sz_decimals = szd,
            liq_alert_pct = float_env("DN_LIQ_ALERT_PCT") or 20.0,
            auto_protect  = os.environ.get("DN_AUTO_PROTECT", "true").strip().lower() == "true",
            reduce_pct    = float_env("DN_REDUCE_PCT") or 25.0,
            perp_leverage = int(float_env("DN_PERP_LEVERAGE") or 1),
            allow_scale_up = os.environ.get("DN_ALLOW_SCALE_UP", "false").strip().lower() == "true",
        )


def _new_state() -> dict:
    return {"status": "flat", "target_sz": 0.0, "fees_paid": 0.0,
            "funding_accrued": 0.0, "opened_at": None, "opened_ts": None, "liq_alerted": False,
            "funding_hist": [],   # [[ts, rate], ...] pt media pe fereastra
            "orphan_count": 0,    # tick-uri consecutive cu un singur picior (anti-glitch)
            "gone_count": 0,      # tick-uri consecutive cu AMBELE picioare disparute
            "drift_count": 0,     # tick-uri consecutive cu drift mare (confirmare inainte de trade)
            "order_fails": 0,     # ordine esuate consecutiv (alerta la 3)
            "cooldown_until": 0,  # nu redeschide inainte de acest timestamp (anti-thrash)
            "spot_qty": 0.0, "perp_szi": 0.0}   # spot_qty/perp_szi folosite doar in PAPER


class DeltaNeutral:
    def __init__(self, client: HLClient, params: DNParams, dry_run: bool = True, desktop: bool = False):
        self.client = client
        self.p = params
        self.dry_run = dry_run
        self.desktop = desktop
        self.state_file = state_path_for(params.coin)
        # migrare de la numele vechi (.state_dn.json, o singura moneda) — pastreaza starea
        if not os.path.exists(self.state_file) and os.path.exists(_OLD_STATE):
            try:
                os.rename(_OLD_STATE, self.state_file)
                log(f"  [DN] stare migrata: .state_dn.json -> {os.path.basename(self.state_file)}")
            except OSError as e:
                log(f"  ! [DN] migrare stare esuata: {e}")
        self.s = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    m = _new_state(); m.update(json.load(f)); return m
            except (OSError, ValueError):
                pass
        return _new_state()

    def _save(self):
        try:
            tmp = self.state_file + ".tmp"   # scriere ATOMICA: crash la mijloc nu corupe starea
            with open(tmp, "w") as f:
                json.dump(self.s, f, indent=2)
            os.replace(tmp, self.state_file)
        except OSError as e:
            log(f"  ! [DN] nu pot salva: {e}")

    def _acquire_lock(self) -> bool:
        """Lacat pe stare: A DOUA instanta pe aceeasi moneda refuza sa porneasca
        (doua boturi ar dubla ordinele si s-ar bate pe rebalans)."""
        try:
            self._lock_fh = open(self.state_file + ".lock", "w")
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            try:
                self._lock_fh.close()
            except (OSError, AttributeError):
                pass
            return False

    # -- stratul de citire: cele doua picioare ---------------------------------
    def legs(self) -> dict | None:
        """Citeste preturi + cantitati. None daca ORICE citire esueaza — un 0 fals
        la balanta/pozitie ar face rebalansarea sa deschida un picior dublu."""
        spot_px = self.client.spot_mid(self.p.spot_pair)
        perp_px = self.client.mid(self.p.coin)
        funding = self.client.funding_rate(self.p.coin)
        if spot_px is None or perp_px is None or funding is None:
            return None
        if self.dry_run:
            spot_qty = self.s["spot_qty"]; perp_szi = self.s["perp_szi"]
        else:
            try:
                spot_qty = self.client.spot_balance_strict(self.p.spot_token)
                perp_szi, _ = self.client.position_strict(self.p.coin)
                self.s["spot_qty"] = spot_qty   # persista pozitia REALA in state (in live nu mai ramane 0 ca placeholder-ul de paper)
                self.s["perp_szi"] = perp_szi
            except Exception as e:  # noqa: BLE001
                log(f"  [DN] citirea contului a esuat ({e}) — sar peste tick (nu ghicesc)")
                return None
        return {"spot_px": spot_px, "perp_px": perp_px, "funding": funding,
                "spot_qty": spot_qty, "perp_szi": perp_szi}

    def _round(self, sz: float) -> float:
        return round(sz, self.p.sz_decimals)

    # -- executie ---------------------------------------------------------------
    def _skip_dust(self, sz: float, px: float, what: str) -> bool:
        """HL respinge ordine sub ~$10 — nu le trimitem (evitam spam de respingeri)."""
        if sz * px < MIN_ORDER_USD:
            log(f"  [DN] {what} {sz} (~${sz*px:.2f}) sub minimul HL — sar (dust)")
            return True
        return False

    def _record(self, ok: bool, sz: float, px: float):
        """Contorizeaza esecurile consecutive de ordine; fee doar la succes."""
        if ok:
            self.s["order_fails"] = 0
            self.s["fees_paid"] += (HL_FEE_PCT/100) * sz * px
        else:
            self.s["order_fails"] = self.s.get("order_fails", 0) + 1
            if self.s["order_fails"] == 3:
                notify(title=f"⚠ DN {self.p.coin}: 3 ordine esuate consecutiv",
                       body="Verifica marginea/colateralul pe Hyperliquid. "
                            "Botul continua sa incerce (fara sa dubleze nimic).",
                       source="dn", desktop=self.desktop)

    def _buy_spot(self, sz: float, px: float):
        sz = self._round(sz)
        if sz <= 0 or self._skip_dust(sz, px, "BUY SPOT"): return
        if self.dry_run:
            self.s["spot_qty"] += sz; log(f"  [DN] [PAPER] BUY SPOT {sz} {self.p.spot_token} @ {px:.4f}")
            self._record(True, sz, px)
        else:
            ok, oid, msg = self.client.spot_order(self.p.spot_pair, True, sz, px * 1.001, self.p.sz_decimals)
            log(f"  [DN] BUY SPOT {sz} @ ~{px:.4f} -> ok={ok} {msg}")
            self._record(ok, sz, px)

    def _sell_spot(self, sz: float, px: float):
        sz = self._round(sz)
        if sz <= 0 or self._skip_dust(sz, px, "SELL SPOT"): return
        if self.dry_run:
            self.s["spot_qty"] -= sz; log(f"  [DN] [PAPER] SELL SPOT {sz} @ {px:.4f}")
            self._record(True, sz, px)
        else:
            ok, oid, msg = self.client.spot_order(self.p.spot_pair, False, sz, px * 0.999, self.p.sz_decimals)
            log(f"  [DN] SELL SPOT {sz} @ ~{px:.4f} -> ok={ok} {msg}")
            self._record(ok, sz, px)

    def _short_perp(self, sz: float, px: float):
        sz = self._round(sz)
        if sz <= 0 or self._skip_dust(sz, px, "SHORT PERP"): return
        if self.dry_run:
            self.s["perp_szi"] -= sz; log(f"  [DN] [PAPER] SHORT PERP {sz} {self.p.coin} @ {px:.4f}")
            self._record(True, sz, px)
        else:
            ok, oid, msg = self.client.place_limit(self.p.coin, False, sz, px * 0.999, reduce_only=False)
            log(f"  [DN] SHORT PERP {sz} @ ~{px:.4f} -> ok={ok} {msg}")
            self._record(ok, sz, px)

    def _cover_perp(self, sz: float, px: float):
        sz = self._round(sz)
        if sz <= 0 or self._skip_dust(sz, px, "COVER PERP"): return
        if self.dry_run:
            self.s["perp_szi"] += sz; log(f"  [DN] [PAPER] COVER PERP {sz} @ {px:.4f}")
            self._record(True, sz, px)
        else:
            ok, oid, msg = self.client.place_limit(self.p.coin, True, sz, px * 1.001, reduce_only=True)
            log(f"  [DN] COVER PERP {sz} @ ~{px:.4f} -> ok={ok} {msg}")
            self._record(ok, sz, px)

    def _cancel_open_orders(self):
        """Best-effort: anuleaza ordinele ramase pe coin/pereche (curatenie la incident)."""
        if self.dry_run:
            return
        try:
            for o in self.client.open_orders():
                if o.get("coin") in (self.p.coin, self.p.spot_pair):
                    self.client.cancel(o.get("coin"), o.get("oid"))
                    log(f"  [DN] ordin ramas anulat: {o.get('coin')} oid={o.get('oid')}")
        except Exception as e:  # noqa: BLE001
            log(f"  ! [DN] curatenia ordinelor a esuat ({e}) — continui")

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
        self.s["status"] = "open"; self.s["target_sz"] = sz
        self.s["opened_at"] = now_str(); self.s["opened_ts"] = time.time()
        notify(title=f"DN {self.p.coin} DESCHIS",
               body=f"long {sz} spot + short {sz} perp | fund {L['funding']*100:.4f}%/h",
               source="dn", desktop=self.desktop)

    def _close(self, L: dict, reason: str):
        sz_spot = self._round(L["spot_qty"]); sz_perp = self._round(abs(L["perp_szi"]))
        log(f"  [DN] <<< INCHID delta-neutral ({reason}): vand {sz_spot} spot + acopar {sz_perp} perp")
        if sz_spot > 0: self._sell_spot(sz_spot, L["spot_px"])
        if sz_perp > 0: self._cover_perp(sz_perp, L["perp_px"])
        notify(title=f"DN {self.p.coin} INCHIS ({reason})",
               body=f"fund~{self.s['funding_accrued']:+.2f}$ fee~{self.s['fees_paid']:.2f}$",
               source="dn", desktop=self.desktop)
        keep_fund = self.s["funding_accrued"]; keep_fee = self.s["fees_paid"]
        self.s = _new_state(); self.s["funding_accrued"] = keep_fund; self.s["fees_paid"] = keep_fee

    def _rebalance(self, L: dict):
        """Aduce ambele picioare la target_sz (corecteaza fill-uri partiale / drift).
        Un drift URIAS (>50% din tinta) e suspect (glitch de citire / fill masiv) —
        cere confirmare pe 2 tick-uri consecutive inainte sa tranzactioneze."""
        tgt = self.s["target_sz"]; tol = tgt * self.p.rebalance_pct/100
        d_spot = tgt - L["spot_qty"]
        d_perp = tgt - abs(L["perp_szi"])
        if tgt > 0 and max(abs(d_spot), abs(d_perp)) > tgt * 0.5:
            self.s["drift_count"] = self.s.get("drift_count", 0) + 1
            if self.s["drift_count"] < 2:
                log("  [DN] drift mare detectat — astept confirmarea pe inca un tick (anti-glitch)")
                return
        else:
            self.s["drift_count"] = 0
        if abs(d_spot) > tol:
            (self._buy_spot if d_spot > 0 else self._sell_spot)(abs(d_spot), L["spot_px"])
            log(f"  [DN] rebalans SPOT {d_spot:+.4f} (target {tgt})")
        if abs(d_perp) > tol:
            (self._short_perp if d_perp > 0 else self._cover_perp)(abs(d_perp), L["perp_px"])
            log(f"  [DN] rebalans PERP {d_perp:+.4f} (target {tgt})")
        self.s["drift_count"] = 0

    def _go_flat(self, reason: str, cooldown_s: float = 3600):
        """Marcheaza flat + cooldown (anti-thrash) pastrand contoarele de P&L."""
        keep_fund, keep_fee = self.s["funding_accrued"], self.s["fees_paid"]
        self.s = _new_state()
        self.s["funding_accrued"], self.s["fees_paid"] = keep_fund, keep_fee
        self.s["cooldown_until"] = time.time() + cooldown_s
        log(f"  [DN] -> flat ({reason}); cooldown {cooldown_s/60:.0f} min inainte de o noua deschidere")

    def _check_legs_integrity(self, L: dict) -> bool:
        """Picior disparut (lichidare short / vanzare manuala / glitch API).
        Confirmare pe 2 tick-uri consecutive inainte de ORICE actiune — o citire
        gresita nu tranzactioneaza. True = a tratat cazul, tick-ul se opreste aici."""
        tgt = self.s["target_sz"]
        if tgt <= 0:
            self._go_flat("tinta zero", cooldown_s=0)
            return True
        sq, pq = L["spot_qty"], abs(L["perp_szi"])
        spot_gone, perp_gone = sq < tgt * 0.1, pq < tgt * 0.1
        if spot_gone and perp_gone:
            self.s["gone_count"] = self.s.get("gone_count", 0) + 1
            if self.s["gone_count"] < 2:
                log("  [DN] ambele picioare par disparute — astept confirmarea (anti-glitch)")
                return True
            log("  [DN] pozitia a disparut de pe cont (inchisa manual?)")
            self._cancel_open_orders()
            notify(title=f"DN {self.p.coin}: pozitia a disparut — trec pe flat",
                   body=f"ambele picioare disparute (inchise manual?) — curat ordinele, cooldown 1h",
                   source="dn", desktop=self.desktop)
            self._go_flat("ambele picioare disparute")
            return True
        if spot_gone != perp_gone:                    # exact UNUL a disparut -> RISC DIRECTIONAL
            self.s["orphan_count"] = self.s.get("orphan_count", 0) + 1
            if self.s["orphan_count"] < 2:
                log("  [DN] un picior pare disparut — astept confirmarea (anti-glitch)")
                return True
            what = ("short-ul perp (LICHIDAT sau inchis manual)" if perp_gone
                    else "spot-ul (vandut manual?)")
            log(f"  ⚠ [DN] {what} a disparut — pozitia NU mai e neutra!")
            self._cancel_open_orders()
            if self.p.auto_protect:
                if perp_gone and sq > 0:
                    self._sell_spot(sq, L["spot_px"])
                if spot_gone and pq > 0:
                    self._cover_perp(pq, L["perp_px"])
                notify(title=f"🛡 DN {self.p.coin}: picior disparut — am inchis si restul",
                       body=f"{what} — lichidat piciorul ramas (elimin riscul directional), cooldown 1h",
                       source="dn", desktop=self.desktop)
                self._go_flat("picior orfan inchis")
            else:
                notify(title=f"⚠ DN {self.p.coin}: picior disparut — INTERVENTIE MANUALA",
                       body=f"{what}, DN_AUTO_PROTECT=false: nu actionez singur — pozitia ramasa e DIRECTIONALA!",
                       source="dn", desktop=self.desktop)
            return True
        self.s["orphan_count"] = 0
        self.s["gone_count"] = 0
        return False

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
                       body=f"p{perp_px:.2f} liq{liq:.2f} (dist {dist_pct:.1f}%)",
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
                           body=f"aproape de lichidare — redus {cut} pe ambele picioare, raman neutru",
                           source="dn", desktop=self.desktop)
                    return True
        elif dist_pct > self.p.liq_alert_pct * 1.5:
            self.s["liq_alerted"] = False   # revenit la siguranta -> re-armeaza alerta
        return False

    # -- bucla ------------------------------------------------------------------
    def run(self):
        if not self._acquire_lock():
            log(f"  ! [DN] ALTA INSTANTA ruleaza deja pe {self.p.coin} — IES (anti-dublare)")
            notify(title=f"DN {self.p.coin}: instanta dubla refuzata",
                   body="Un alt dn_bot ruleaza deja pe aceeasi moneda/stare. "
                        "Aceasta instanta s-a oprit singura ca sa nu dubleze ordinele.",
                   source="dn", desktop=self.desktop)
            return
        log("  === DELTA-NEUTRAL PORNIT ===")
        log(f"      coin={self.p.coin} spot={self.p.spot_pair}  notional={self.p.notional} USDC/picior  {'[PAPER]' if self.dry_run else '⚠ REAL'}")
        log(f"      intrare funding>= {self.p.entry_funding_hr*100:.4f}%/ora  | iesire funding< {self.p.exit_funding_hr*100:.4f}%/ora")
        log(f"      mediere {self.p.funding_window_h}h  | tin minim {self.p.min_hold_h}h  | rebalans la {self.p.rebalance_pct}% delta")
        errors = 0
        while True:
            try:
                L = self.legs()
                if L is None:
                    log("  [DN] date indisponibile — reincerc")
                else:
                    self.tick(L)
                    errors = 0
                self._save()
            except KeyboardInterrupt:
                log("  [DN] oprit manual."); self._save(); return
            except Exception as e:  # noqa: BLE001 — botul autonom NU moare la o exceptie
                errors += 1
                log(f"  ! [DN] eroare neasteptata (#{errors} consecutiv): {e!r} — botul continua")
                if errors == 3:
                    notify(title=f"⚠ DN {self.p.coin}: erori repetate",
                           body=f"{e!r}\nBotul ruleaza in continuare si reincearca cu backoff.",
                           source="dn", desktop=self.desktop)
                try:
                    self._save()
                except Exception:  # noqa: BLE001
                    pass
            # backoff exponential la erori (plafonat la 5 min), altfel ritmul normal
            time.sleep(min(self.p.check_minutes * 60 * (2 ** min(errors, 3)), 300))

    def _maybe_scale_up(self, L: dict) -> None:
        """Creste pozitia VIE pana la 'notional' (DN_ALLOW_SCALE_UP). Bumpeaza target_sz
        -> _rebalance cumpara diferenta pe AMBELE picioare (ramane neutru). Garda de
        colateral: nu cumpara spot peste cat USDC liber ai (creste partial daca e cazul)."""
        if not self.p.allow_scale_up:
            return
        cur_notional = self.s["target_sz"] * L["perp_px"]
        if cur_notional >= self.p.notional * 0.95:
            return                                        # deja la tinta
        want = self._round(self.p.notional / L["perp_px"])
        add = self._round(want - self.s["target_sz"])
        if add <= 0:
            return
        if not self.dry_run:
            try:
                # USDC-ul SPOT cumpara piciorul long; marginea short-ului e acoperita
                # de colateralul unificat. withdrawable() (perp) e adesea $0 desi ai
                # USDC in spot -> verificam balanta SPOT, sursa corecta.
                free = self.client.spot_balance("USDC")
            except Exception as e:  # noqa: BLE001
                log(f"  ! [DN] scale-up: nu pot citi colateralul ({e}) — amanat"); return
            if free < add * L["spot_px"]:                 # nu-mi permit tot -> cresc partial
                aff = self._round((free * 0.95) / L["spot_px"])
                if aff <= 0:
                    log(f"  [DN] scale-up dorit dar colateral insuficient (liber ${free:.0f})")
                    return
                want = self._round(self.s["target_sz"] + aff)
        log(f"  [DN] ⬆ SCALE-UP catre ${self.p.notional:.0f}/picior: target {self.s['target_sz']} -> {want} "
            f"(~${want*L['perp_px']:.0f}/picior). _rebalance cumpara diferenta.")
        self.s["target_sz"] = want
        notify(title=f"⬆ DN {self.p.coin}: cresc pozitia la ~${want*L['perp_px']:.0f}/picior",
               body=f"scale-up spre notional {self.p.notional}, raman neutru",
               source="dn", desktop=self.desktop)

    def tick(self, L: dict) -> None:
        """Un pas de decizie (extras ca sa fie testabil): deschide / tine / inchide / rebalanseaza."""
        # RECONCILIERE: daca exista deja o pozitie pe cont (restart / state sters), o ADOPTAM
        # -> nu deschidem din nou (anti-dublare).
        if not self.dry_run and self.s["status"] == "flat":
            sq, pq = abs(L["spot_qty"]), abs(L["perp_szi"])
            if sq > 1e-6 and pq > 1e-6:
                self.s["status"] = "open"
                self.s["target_sz"] = round((sq + pq) / 2, 6)
                if not self.s.get("opened_ts"):
                    self.s["opened_ts"] = time.time()
                log(f"  [DN] adopt pozitie existenta: spot {L['spot_qty']} / perp {L['perp_szi']} "
                    f"-> status=open, target={self.s['target_sz']}")
        fhr = L["funding"]
        delta = L["spot_qty"] + L["perp_szi"]       # ~0 cand e hedge-uit
        basis = (L["perp_px"] - L["spot_px"]) / L["spot_px"] * 100

        # --- funding MEDIAT pe fereastra (ignora citiri izolate, anti-churn) ---
        now = time.time()
        hist = self.s.setdefault("funding_hist", [])
        hist.append([now, fhr])
        self.s["funding_hist"] = [x for x in hist if now - x[0] <= self.p.funding_window_h * 3600]
        avg_f = sum(x[1] for x in self.s["funding_hist"]) / len(self.s["funding_hist"])

        if self.s["status"] == "flat":
            if now < self.s.get("cooldown_until", 0):
                log(f"  [DN] in cooldown dupa un incident ({(self.s['cooldown_until']-now)/60:.0f} min ramase) — nu redeschid")
            elif avg_f >= self.p.entry_funding_hr:
                self._open(L)
            else:
                log(f"  [DN] funding mediu {avg_f*100:+.4f}%/ora < prag intrare — astept (flat)")
        else:
            if self._check_legs_integrity(L):       # lichidare/inchidere manuala/glitch
                return
            self.s["funding_accrued"] += fhr * abs(L["perp_szi"]) * L["perp_px"] * (self.p.check_minutes/60)
            reduced = self._check_liq(L)            # protectie: alerta + reduce automat
            if not reduced:
                self._maybe_scale_up(L)             # creste pozitia la noul notional (bumpeaza target)
            held_h = (now - (self.s.get("opened_ts") or now)) / 3600
            # INCHIDE doar daca media e sub prag SI am tinut suficient (histerezis + timp minim)
            if avg_f < self.p.exit_funding_hr and held_h >= self.p.min_hold_h:
                self._close(L, f"funding mediu {avg_f*100:.4f}%/ora sub prag, tinut {held_h:.1f}h")
            elif avg_f < self.p.exit_funding_hr:
                log(f"  [DN] funding mediu negativ dar tinut doar {held_h:.1f}h < {self.p.min_hold_h}h "
                    f"— NU inchid (las timpul sa lucreze, evit churn-ul)")
                if not reduced:
                    self._rebalance(L)
            elif not reduced:
                self._rebalance(L)

        log(f"  [DN] funding={fhr*100:+.4f}%/ora (mediu {avg_f*100:+.4f}, ~{avg_f*24*365*100:.0f}%/an)  "
            f"delta={delta:+.4f}  basis={basis:+.3f}%  status={self.s['status']}  "
            f"funding_acumulat~{self.s['funding_accrued']:+.4f}  fee~{self.s['fees_paid']:.4f} USDC")
