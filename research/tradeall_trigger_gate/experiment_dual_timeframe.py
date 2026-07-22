#!/usr/bin/env python3
"""
Experiment 4 (izolat, NU modifica tradeall.py) — cerere user: "ce reprezinta
fiecare variabila nu ne ajuta prea mult, as vrea sa folosim intuitia pe
strategia simpla" — adica: daca ambele ferestre (mica SI mare) sunt de acord
asupra DIRECTIEI, folosind aceeasi masura CONTINUA (nu unul continuu + unul
rar/prag), ar trebui sa fie o strategie sanatoasa de "trend-following pe doua
orizonturi de timp".

Problema gasita (raspuns la intrebarea despre are_close): slope_big NU e o
masura continua a directiei — e EXACT 0 (sentinel "nicio miscare mare") pana
cand pretul trece un prag fix fata de extrema ferestrei (WindowAnalyzer.
check_price_change). gradient (fereastra mica) e continuu (aproape niciodata
exact 0). "Acord" intre ele (gradient>0 si slope_big>0) cere ca un eveniment
RAR (slope_big nenul) sa coincida cu un eveniment ZGOMOTOS (semnul lui
gradient) — de-asta Experimentul 2 a dat aproape 0 activitate pe acord.

Fix testat aici: inlocuim slope_big cu gradient_big — ACEEASI derivare ca
gradient (PriceWindow.get_instant_trend(), semn -1/0/+1 dintr-o regresie
continua), dar calculata pe FEREASTRA MARE in loc de cea mica. Acum "acord"
inseamna ceva onest: "trendul de scurta durata SI cel de lunga durata sunt
de acord asupra directiei" — o strategie dual-timeframe clasica, nu o
coincidenta rara.

Cooldown fire-once (din Experimentul 3) e mereu ACTIV aici — am demonstrat deja
ca fara el orice crestere de frecventa duce la overtrading catastrofal; nu
mai testam "fara cooldown" ca sa nu irosim timp pe un rezultat deja cunoscut.

Variante (pe BTC 2 zile / TAO 12h, aceleasi ferestre ca Experimentele 1-3):
  V5_dual_timeframe_acord : gradient(mic)>0 SI gradient_big(mare)>0 -> UP
                            gradient(mic)<0 SI gradient_big(mare)<0 -> DOWN
                            + cooldown fire-once (ca Experimentul 3)
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

_OrigWindowAnalyzer = ta.WindowAnalyzer


class GradientBigWindowAnalyzer(_OrigWindowAnalyzer):
    """check_price_change() intoarce acum gradient_big (semn continuu, aceeasi
    derivare ca PriceWindow.get_instant_trend() folosit pt fereastra mica) in
    loc de slope_big (rar, prag-gated). Semnatura pastrata (val, pos) — pos
    (al doilea element) nu e folosit de logic()."""
    def check_price_change(self, threshold):
        final_trend, growth_coefficient, slope_full, gradient_recent = self.window.get_instant_trend()
        return final_trend, 0


def make_instrumented(tag):
    stats = {"starts": 0, "confirms": 0, "expires": 0, "max_confirm_count": 0,
              "fired_instances_up": 0, "fired_instances_down": 0}
    STATS[tag] = stats
    Base = ta.TrendState

    class InstrumentedTrendState(Base):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._fired_up = False
            self._fired_down = False

        def start_trend(self, new_state):
            stats["starts"] += 1
            self._fired_up = False
            self._fired_down = False
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

        def mark_fired(self, direction):
            if direction == "UP" and not self._fired_up:
                self._fired_up = True
                stats["fired_instances_up"] += 1
            elif direction == "DOWN" and not self._fired_down:
                self._fired_down = True
                stats["fired_instances_down"] += 1

        def already_fired(self, direction):
            return self._fired_up if direction == "UP" else self._fired_down

    return InstrumentedTrendState


def make_logic_cooldown(start_up_cond, start_down_cond, label):
    """Identic cu Experimentul 3 (copie logic() + cooldown fire-once) — reluat
    aici ca sa ramana script independent, fara dependenta de fisierul anterior."""
    def logic_variant(win, enable, symbol, gradient, slope, trend_state, current_price):
        d = 14
        h = 24
        proposed_price = current_price

        def fire_once(direction, action, reason):
            if trend_state.already_fired(direction):
                return
            if enable:
                ta._fire_order(symbol, action, proposed_price, f"{label}_{reason}",
                                safeback_seconds=d * h * 3600 + 60, force=False, cancelorders=True, hours=1)
            trend_state.mark_fired(direction)

        if start_up_cond(gradient, slope):
            proposed_price = current_price
            if trend_state.is_trend_up():
                trend_state.confirm_trend()
                if trend_state.is_trend_uniform_confirmed() and trend_state.is_trend_fresh():
                    fire_once("UP", "BUY", "trend_confirmed_up")
            else:
                trend_state.start_trend('UP')

        if start_down_cond(gradient, slope):
            proposed_price = current_price
            if trend_state.is_trend_down():
                trend_state.confirm_trend()
                if trend_state.is_trend_uniform_confirmed() and trend_state.is_trend_fresh():
                    fire_once("DOWN", "SELL", "trend_confirmed_down")
            else:
                trend_state.start_trend('DOWN')

        proposed_price = current_price
        if slope <= 0 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire_once("UP", "BUY", "consistent_or_old_up")
        if slope >= 0 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire_once("DOWN", "SELL", "consistent_or_old_down")

        if slope <= -5.1 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire_once("UP", "BUY", "slope<=-5.1_up")
        if slope >= 5.1 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire_once("DOWN", "SELL", "slope>=5.1_down")

        if slope <= -5.1 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    and trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire_once("UP", "BUY", "slope<=-5.1_and_old_down")
        if slope >= 5.1 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    and trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire_once("DOWN", "SELL", "slope>=5.1_and_old_up")

    return logic_variant


def run_variant(tag, symbol, start_ts, end_ts):
    ta.TrendState = make_instrumented(tag)
    tb.ta.TrendState = ta.TrendState
    ta.logic = make_logic_cooldown(lambda g, s: g > 0 and s > 0,
                                    lambda g, s: g < 0 and s < 0, tag)
    tb.ta.logic = ta.logic
    ta.WindowAnalyzer = GradientBigWindowAnalyzer
    tb.ta.WindowAnalyzer = ta.WindowAnalyzer

    run_id = f"experiment4_{tag}_{symbol}"
    import shutil
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    shutil.rmtree(out_dir, ignore_errors=True)

    t0 = time.time()
    tb.run_backtest(symbol, start_ts, end_ts, "fast", run_id, "cache24",
                     cache24_file=os.path.join(ROOT, "cachedb", f"cache_24price_long_{symbol}.jsonl"),
                     quiet=True, kalman_primary=False)
    elapsed = time.time() - t0

    ta.WindowAnalyzer = _OrigWindowAnalyzer
    tb.ta.WindowAnalyzer = ta.WindowAnalyzer

    import json
    pnl_path = os.path.join(out_dir, "pnl.json")
    pnl = json.load(open(pnl_path)) if os.path.exists(pnl_path) else {}
    sys.stderr.write(f"\n=== {tag} / {symbol} === (wall {elapsed:.1f}s)\n")
    sys.stderr.write(f"stats: {STATS[tag]}\n")
    sys.stderr.write(f"pnl: {pnl}\n")
    return STATS[tag], pnl


if __name__ == "__main__":
    from datetime import datetime

    btc_start = datetime.strptime("2026-07-14", "%Y-%m-%d").timestamp()
    btc_end = btc_start + 2 * 24 * 3600

    tao_start = datetime.strptime("2026-07-14 19:40:00", "%Y-%m-%d %H:%M:%S").timestamp()
    tao_end = tao_start + 12 * 3600

    run_variant("V5_dual_timeframe_acord", "BTCUSDC", btc_start, btc_end)
    run_variant("V5_dual_timeframe_acord", "TAOUSDC", tao_start, tao_end)

    # fereastra mai lunga pe BTC (7 zile complete, aceeasi arhiva ca backtest-urile
    # A/B principale) — 2 zile arata prea putine date ca sa distingem "strategie
    # sanatoasa, rara" de "strategie moarta"; pe 7 zile avem un test mult mai onest.
    btc7_start = datetime.strptime("2026-07-14", "%Y-%m-%d").timestamp()
    run_variant("V5_dual_timeframe_acord_7d", "BTCUSDC", btc7_start, None)
