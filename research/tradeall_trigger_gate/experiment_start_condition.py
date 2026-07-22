#!/usr/bin/env python3
"""
Experiment 2 (izolat, NU modifica tradeall.py pe disc) — cerere user: "poti sa vii
cu o idee unde/ce sa modific sau sa faci teste si cu gradient>0 si slope_big<0,
sa le elimini complet sau sa le modifici in diverse forme si sa rulezi test?"

Din Experiment 1 (vezi memoria tradeall-trigger-gate-investigation.md): relaxarea
pragurilor de CONFIRMARE (24, expirare, "trend vechi" 1.9h) NU creeaza oportunitati
noi — doar prelungeste refirerea unui trend deja pornit. Adevaratul gate e
PORNIREA trendului (start_trend, tradeall.py:389/410), care se intampla DOAR la
divergenta gradient(fereastra mica, semn -1/0/+1) vs slope_big(fereastra mare,
aproape mereu EXACT 0 — vezi WindowAnalyzer.check_price_change, nonzero doar cand
pretul trece PRICE_CHANGE_THRESHOLD_BIG_EUR fata de min/max ferestrei).

Testam 4 variante ale CONDITIEI DE START (logic(), liniile ~375/396 in tradeall.py),
prin monkeypatch pe ta.logic (functie COPIATA aici, modificata doar la conditia de
start — restul functiei e IDENTIC cu originalul citit direct din tradeall.py):

  V0 baseline      : gradient>0 and slope_big<0   (divergenta, ca azi)
  V1 doar_gradient : gradient>0                    (ignora complet slope_big la start)
  V2 acord         : gradient>0 and slope_big>0    (ACORD intre ferestre, nu divergenta)
  V3 prag_mic      : conditia ramane divergenta (ca V0), dar PRICE_CHANGE_THRESHOLD_BIG_EUR
                      e micsorat de 10x (monkeypatch pe ta.PRICE_CHANGE_THRESHOLD_BIG_EUR),
                      ca slope_big sa nu mai fie aproape mereu 0

Rulat pe DOUA simboluri: BTCUSDC (complet TACUT sub V0 in primele ~60% din arhiva
de 7 zile — cel mai clar test daca o conditie mai larga "trezeste" ceva) si
TAOUSDC (avea deja 1 start sub V0 in 12h — verificam daca variantele produc
starturi SUPLIMENTARE, independente, nu doar acelasi eveniment).
"""
import os
import sys
import time

ROOT = "/home/predut/binance"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import tradeall_backtest as tb
import tradeall as ta

STATS = {}


def make_instrumented(tag):
    stats = {"starts": 0, "confirms": 0, "expires": 0, "max_confirm_count": 0}
    STATS[tag] = stats
    Base = ta.TrendState

    class InstrumentedTrendState(Base):
        def start_trend(self, new_state):
            stats["starts"] += 1
            return super().start_trend(new_state)

        def confirm_trend(self):
            r = super().confirm_trend()
            stats["confirms"] += 1
            stats["max_confirm_count"] = max(stats["max_confirm_count"], self.confirm_count)
            return r

        def check_trend_expiration(self):
            was_expired = self.expired
            r = super().check_trend_expiration()
            if r and not was_expired:
                stats["expires"] += 1
            return r

    return InstrumentedTrendState


# ── Copie EXACTA a logic() din tradeall.py (liniile 356-471 la data scrierii),
#    cu un singur punct de variatie: START_COND(gradient, slope) — restul
#    (blocurile de FIRE, is_trend_consistent_validated/is_started_trend_older_than,
#    valorile 5.1/TREND_TO_BE_OLD_SECONDS) raman NESCHIMBATE, ca sa izolam STRICT
#    efectul conditiei de start.
def make_logic(start_up_cond, start_down_cond, label):
    def logic_variant(win, enable, symbol, gradient, slope, trend_state, current_price):
        d = 14
        h = 24
        proposed_price = current_price

        if start_up_cond(gradient, slope):
            proposed_price = current_price
            if trend_state.is_trend_up():
                trend_state.confirm_trend()
                if trend_state.is_trend_uniform_confirmed() and trend_state.is_trend_fresh():
                    if enable:
                        ta._fire_order(symbol, "BUY", proposed_price, f"{label}_trend_confirmed_up",
                                        safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)
            else:
                trend_state.start_trend('UP')

        if start_down_cond(gradient, slope):
            proposed_price = current_price
            if trend_state.is_trend_down():
                trend_state.confirm_trend()
                if trend_state.is_trend_uniform_confirmed() and trend_state.is_trend_fresh():
                    if enable:
                        ta._fire_order(symbol, "SELL", proposed_price, f"{label}_trend_confirmed_down",
                                        safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)
            else:
                trend_state.start_trend('DOWN')

        proposed_price = current_price
        if slope <= 0 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                if enable:
                    ta._fire_order(symbol, "BUY", proposed_price, f"{label}_consistent_or_old_up",
                                    safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)
        if slope >= 0 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                if enable:
                    ta._fire_order(symbol, "SELL", proposed_price, f"{label}_consistent_or_old_down",
                                    safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)

        if slope <= -5.1 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                if enable:
                    ta._fire_order(symbol, "BUY", proposed_price, f"{label}_slope<=-5.1_up",
                                    safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)
        if slope >= 5.1 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                if enable:
                    ta._fire_order(symbol, "SELL", proposed_price, f"{label}_slope>=5.1_down",
                                    safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)

        if slope <= -5.1 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    and trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                if enable:
                    ta._fire_order(symbol, "BUY", proposed_price, f"{label}_slope<=-5.1_and_old_down",
                                    safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)
        if slope >= 5.1 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    and trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                if enable:
                    ta._fire_order(symbol, "SELL", proposed_price, f"{label}_slope>=5.1_and_old_up",
                                    safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)

    return logic_variant


VARIANTS = {
    "V0_baseline_divergenta": (lambda g, s: g > 0 and s < 0, lambda g, s: g < 0 and s > 0, None),
    "V1_doar_gradient": (lambda g, s: g > 0, lambda g, s: g < 0, None),
    "V2_acord": (lambda g, s: g > 0 and s > 0, lambda g, s: g < 0 and s < 0, None),
    "V3_prag_big_mic_10x": (lambda g, s: g > 0 and s < 0, lambda g, s: g < 0 and s > 0, "PRICE_CHANGE_THRESHOLD_BIG_EUR"),
}


def run_variant(tag, up_cond, down_cond, threshold_override_name, symbol, start_ts, end_ts):
    ta.TrendState = make_instrumented(tag)
    tb.ta.TrendState = ta.TrendState
    ta.logic = make_logic(up_cond, down_cond, tag)
    tb.ta.logic = ta.logic

    orig_threshold = ta.PRICE_CHANGE_THRESHOLD_BIG_EUR
    if threshold_override_name:
        ta.PRICE_CHANGE_THRESHOLD_BIG_EUR = orig_threshold / 10.0

    run_id = f"experiment2_{tag}_{symbol}"
    import shutil
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    shutil.rmtree(out_dir, ignore_errors=True)

    t0 = time.time()
    tb.run_backtest(symbol, start_ts, end_ts, "fast", run_id, "cache24",
                     cache24_file=os.path.join(ROOT, "cachedb", f"cache_24price_long_{symbol}.jsonl"),
                     quiet=True, kalman_primary=False)
    elapsed = time.time() - t0

    ta.PRICE_CHANGE_THRESHOLD_BIG_EUR = orig_threshold

    import json
    pnl_path = os.path.join(out_dir, "pnl.json")
    pnl = json.load(open(pnl_path)) if os.path.exists(pnl_path) else {}
    sys.stderr.write(f"\n=== {tag} / {symbol} === (wall {elapsed:.1f}s)\n")
    sys.stderr.write(f"stats: {STATS[tag]}\n")
    sys.stderr.write(f"pnl: {pnl}\n")
    return STATS[tag], pnl


if __name__ == "__main__":
    from datetime import datetime

    # BTC: primele 2 zile — sub V0 (baseline) stim deja ca sunt COMPLET TACUTE
    # (0 starts, verificat separat in backtest-ul A/B principal). Cel mai curat
    # test daca o conditie mai larga produce starturi noi acolo unde azi nu e nimic.
    btc_start = datetime.strptime("2026-07-14", "%Y-%m-%d").timestamp()
    btc_end = btc_start + 2 * 24 * 3600

    # TAO: aceeasi fereastra de 12h ca Experimentul 1 (avea exact 1 start sub V0).
    tao_start = datetime.strptime("2026-07-14 19:40:00", "%Y-%m-%d %H:%M:%S").timestamp()
    tao_end = tao_start + 12 * 3600

    for tag, (up, down, thr) in VARIANTS.items():
        run_variant(tag, up, down, thr, "BTCUSDC", btc_start, btc_end)

    for tag, (up, down, thr) in VARIANTS.items():
        run_variant(tag, up, down, thr, "TAOUSDC", tao_start, tao_end)
