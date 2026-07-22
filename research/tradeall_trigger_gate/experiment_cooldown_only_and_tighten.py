#!/usr/bin/env python3
"""
Experiment 7 (izolat, NU modifica tradeall.py) — raspuns la 3 intrebari user
dupa Experimentul 6:
  1. "merita comis DOAR cooldown-ul (fara sa ating pragurile)?"
  2. "toate experimentele arata ca varianta curenta e cea mai buna?"
  3. "sa inaspresc parametrii are sens de test?"

Aici testam, pe intreg istoricul disponibil (329 zile, cache_price_*.jsonl):

  A. "current_cooldown"      : logic() REALA din tradeall.py (conditia de start
                                divergenta gradient/slope_big NESCHIMBATA, toate
                                pragurile 5.1/24-confirmari/TREND_TO_BE_OLD_SECONDS
                                NESCHIMBATE) + DOAR cooldown-ul (executie
                                confirmata + interval minim intre reincercari,
                                varianta finala din Experimentul 6). Raspunde
                                direct la intrebarea 1: ce s-ar intampla daca am
                                comite DOAR cooldown-ul, fara nicio alta schimbare?

  B. "tighten_confirm48_cooldown" : ca mai sus, dar pragul de confirmari pt
                                is_trend_consistent_validated() DUBLAT (24->48)
                                — testeaza intrebarea 3 (inasprire, nu relaxare).
                                Motivatie: TAO a acumulat 99 confirmari intr-un
                                singur trend si a atins usor pragul de 24; un
                                prag mai greu de atins ar putea evita intreg
                                episodul problematic.

logic() e o copie FIDELA a functiei din tradeall.py (toate blocurile, valorile
5.1 si TREND_TO_BE_OLD_SECONDS neschimbate) — SINGURA diferenta fata de codul
real e cooldown-ul adaugat la fiecare punct unde s-ar chema _fire_order, plus
(doar pt varianta B) suprascrierea is_trend_consistent_validated().
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

MIN_RETRY_INTERVAL_SEC = 1800.0   # 30 min intre incercari BLOCATE (ca in Exp6)


def make_instrumented(tag, confirm_threshold_override=None):
    stats = STATS.setdefault(tag, {
        "starts": 0, "confirms": 0, "expires": 0,
        "fire_confirmed_up": 0, "fire_blocked_up": 0, "fire_skipped_cooldown_up": 0,
        "fire_confirmed_down": 0, "fire_blocked_down": 0, "fire_skipped_cooldown_down": 0,
    })
    Base = ta.TrendState

    class InstrumentedTrendState(Base):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._last_attempt_up_ts = None
            self._last_attempt_down_ts = None
            self._confirmed_up = False
            self._confirmed_down = False

        def start_trend(self, new_state):
            stats["starts"] += 1
            self._confirmed_up = False
            self._confirmed_down = False
            return super().start_trend(new_state)

        def already_confirmed(self, direction):
            return self._confirmed_up if direction == "UP" else self._confirmed_down

        def mark_confirmed(self, direction):
            if direction == "UP":
                self._confirmed_up = True
            else:
                self._confirmed_down = True

        def confirm_trend(self):
            r = super().confirm_trend()
            stats["confirms"] += 1
            return r

        def check_trend_expiration(self):
            was_expired = self.expired
            r = super().check_trend_expiration()
            if r and not was_expired:
                stats["expires"] += 1
            return r

        def is_trend_consistent_validated(self):
            if confirm_threshold_override is None:
                return super().is_trend_consistent_validated()
            if not self.is_trend_a_minim_validated():
                return False
            return (self.confirm_count > confirm_threshold_override
                    and self.is_trend_uniform_confirmed())

        def can_retry(self, direction, ts):
            last = self._last_attempt_up_ts if direction == "UP" else self._last_attempt_down_ts
            return last is None or (ts - last) >= MIN_RETRY_INTERVAL_SEC

        def mark_attempt(self, direction, ts):
            if direction == "UP":
                self._last_attempt_up_ts = ts
            else:
                self._last_attempt_down_ts = ts

    return InstrumentedTrendState, stats


def make_logic_real_with_cooldown(tag, stats):
    """Copie FIDELA a logic() din tradeall.py (liniile 356-471 la 21-22 iul) —
    NIMIC schimbat in blocurile de decizie, DOAR cooldown adaugat la fiecare
    punct de fire (executie confirmata + interval minim intre reincercari)."""
    def logic_variant(win, enable, symbol, gradient, slope, trend_state, current_price):
        d = 14
        h = 24
        proposed_price = current_price

        def fire(direction, action, reason):
            if trend_state.already_confirmed(direction):
                return
            ts = trend_state._now()
            if not trend_state.can_retry(direction, ts):
                stats[f"fire_skipped_cooldown_{direction.lower()}"] += 1
                return
            trend_state.mark_attempt(direction, ts)
            if enable:
                result = ta._fire_order(symbol, action, proposed_price, f"{tag}_{reason}",
                                         safeback_seconds=d * h * 3600 + 60, force=False,
                                         cancelorders=True, hours=1)
                if result is not None:
                    trend_state.mark_confirmed(direction)
                    stats[f"fire_confirmed_{direction.lower()}"] += 1
                else:
                    stats[f"fire_blocked_{direction.lower()}"] += 1

        if gradient > 0 and slope < 0:
            proposed_price = current_price
            if trend_state.is_trend_up():
                trend_state.confirm_trend()
                if trend_state.is_trend_uniform_confirmed() and trend_state.is_trend_fresh():
                    fire("UP", "BUY", "trend_confirmed_up")
            else:
                trend_state.start_trend('UP')

        if gradient < 0 and slope > 0:
            proposed_price = current_price
            if trend_state.is_trend_down():
                trend_state.confirm_trend()
                if trend_state.is_trend_uniform_confirmed() and trend_state.is_trend_fresh():
                    fire("DOWN", "SELL", "trend_confirmed_down")
            else:
                trend_state.start_trend('DOWN')

        proposed_price = current_price
        if slope <= 0 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire("UP", "BUY", "consistent_or_old_up")
        if slope >= 0 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire("DOWN", "SELL", "consistent_or_old_down")

        if slope <= -5.1 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire("UP", "BUY", "slope<=-5.1_up")
        if slope >= 5.1 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    or trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire("DOWN", "SELL", "slope>=5.1_down")

        if slope <= -5.1 and trend_state.is_trend_down():
            if (trend_state.is_trend_consistent_validated()
                    and trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire("UP", "BUY", "slope<=-5.1_and_old_down")
        if slope >= 5.1 and trend_state.is_trend_up():
            if (trend_state.is_trend_consistent_validated()
                    and trend_state.is_started_trend_older_than(ta.TREND_TO_BE_OLD_SECONDS)):
                fire("DOWN", "SELL", "slope>=5.1_and_old_up")

    return logic_variant


def run_variant(tag, confirm_threshold_override, symbol, start_ts, end_ts):
    ta.TrendState, stats = make_instrumented(tag, confirm_threshold_override)
    tb.ta.TrendState = ta.TrendState
    ta.logic = make_logic_real_with_cooldown(tag, stats)
    tb.ta.logic = ta.logic

    run_id = f"experiment7_{tag}_{symbol}"
    import shutil
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    shutil.rmtree(out_dir, ignore_errors=True)

    # IMPORTANT: logic() reala are constante de timp SCURTE (expirare 2.7min,
    # fresh 3.7min) — gandite pt tick-uri dese (~1s, ca in live). Istoricul
    # SPARS (cache_price_*.jsonl, ~7min/tick) ar face orice trend sa "expire"
    # aproape instant, ca artefact al raritatii datelor, nu al pietei reale
    # (verificat: smoke test 12h -> confirms=0, expires=1 imediat). De-asta
    # folosim arhiva DENSA (cache24, ~1s/tick, 7 zile) — singura sursa
    # compatibila cu aceasta mecanica, chiar daca esantionul e mai mic decat
    # Experimentul 6 (acolo semnalul era recalculat rar, 30min, compatibil cu
    # sparse).
    t0 = time.time()
    tb.run_backtest(symbol, start_ts, end_ts, "fast", run_id, "cache24",
                     cache24_file=os.path.join(ROOT, "cachedb", f"cache_24price_long_{symbol}.jsonl"),
                     quiet=True, kalman_primary=False)
    elapsed = time.time() - t0

    import json
    pnl_path = os.path.join(out_dir, "pnl.json")
    pnl = json.load(open(pnl_path)) if os.path.exists(pnl_path) else {}
    sys.stderr.write(f"\n=== {tag} / {symbol} === (wall {elapsed:.1f}s)\n")
    sys.stderr.write(f"stats: {stats}\n")
    sys.stderr.write(f"pnl: {pnl}\n")


if __name__ == "__main__":
    from datetime import datetime
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True, choices=["current_cooldown", "tighten_confirm48_cooldown"])
    p.add_argument("--symbol", required=True)
    args = p.parse_args()

    dense_start = datetime.strptime("2026-07-14", "%Y-%m-%d").timestamp()
    threshold = 48 if args.tag == "tighten_confirm48_cooldown" else None
    run_variant(args.tag, threshold, args.symbol, dense_start, None)
