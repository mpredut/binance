#!/usr/bin/env python3
"""
Experiment IZOLAT (nu atinge niciun fisier din repo) pt intrebarea utilizatorului:
"pot mari sansa cand se executa triggerele BUY/SELL din tradeall.py? sunt niste
contoare/limite harcodate acolo..."

Ipoteza (din citirea codului, tradeall.py:206-334 TrendState + 356-471 logic()):
  confirm_trend()/start_trend() sunt apelate DOAR in interiorul conditiei inguste
  "gradient>0 si slope_big<0" (sau invers) — o divergenta intre trendul FERESTREI
  MICI (gradient, semn -1/0/+1) si un prag de miscare mare pe FEREASTRA MARE
  (slope_big, aproape mereu 0 -- vezi WindowAnalyzer.check_price_change). Daca
  aceasta divergenta e rara, atunci:
    - confirm_count rareori ajunge la pragul de 24 (8*3) cerut de
      is_trend_consistent_validated().
    - last_confirmation_time ramane inghetat la start_time -> check_trend_expiration()
      (prag expiration_trend_time=2.7min) reseteaza trendul la HOLD MULT inainte
      sa apuce sa imbatraneasca 1.9h (TREND_TO_BE_OLD_SECONDS), deci nici
      fallback-ul "is_started_trend_older_than" nu e atins practic NICIODATA.
  => intreg mecanismul logic() ar fi aproape mort, trigger-ele reale venind
     aproape exclusiv din coincidente rare.

Testam empiric: instrumentam TrendState (subclasa, override start_trend/
confirm_trend/check_trend_expiration) ca sa numaram exact aceste evenimente,
pe un backtest SCURT (cateva ore) BTCUSDC, cu pragurile ACTUALE (baseline)
si apoi cu un candidat de relaxare (expiration_trend_time mult mai mare).
NU modifica tradeall.py pe disc — totul e monkeypatch in memorie, in acest
proces separat.
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
    stats = {"starts": 0, "confirms": 0, "expires": 0, "max_confirm_count": 0,
             "reached_24": 0, "reached_old_1_9h": 0}
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

        def is_trend_consistent_validated(self):
            r = super().is_trend_consistent_validated()
            if r:
                stats["reached_24"] += 1
            return r

        def is_started_trend_older_than(self, old_trend_time):
            r = super().is_started_trend_older_than(old_trend_time)
            if r:
                stats["reached_old_1_9h"] += 1
            return r

    return InstrumentedTrendState


def run_variant(tag, symbol, start_ts, end_ts, expiration_trend_time_override=None,
                trend_to_be_old_override=None, confirm_threshold_override=None):
    ta.TrendState = make_instrumented(tag)
    tb.ta.TrendState = ta.TrendState  # acelasi modul, dar explicit pt claritate

    orig_expiration_init = ta.TrendState.__init__
    if expiration_trend_time_override is not None:
        def patched_init(self, max_duration_seconds, expiration_trend_time, fresh_trend_time, now_fn=time.time):
            orig_expiration_init(self, max_duration_seconds, expiration_trend_time_override, fresh_trend_time, now_fn)
        ta.TrendState.__init__ = patched_init

    orig_trend_to_be_old = ta.TREND_TO_BE_OLD_SECONDS
    if trend_to_be_old_override is not None:
        ta.TREND_TO_BE_OLD_SECONDS = trend_to_be_old_override

    orig_consistent = ta.TrendState.is_trend_consistent_validated
    if confirm_threshold_override is not None:
        def patched_consistent(self):
            if not self.is_trend_a_minim_validated():
                return False
            return self.confirm_count > confirm_threshold_override and self.is_trend_uniform_confirmed()
        ta.TrendState.is_trend_consistent_validated = patched_consistent

    run_id = f"experiment_{tag}"
    import shutil
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    shutil.rmtree(out_dir, ignore_errors=True)

    t0 = time.time()
    tb.run_backtest(symbol, start_ts, end_ts, "fast", run_id, "cache24",
                     cache24_file=os.path.join(ROOT, "cachedb", f"cache_24price_long_{symbol}.jsonl"),
                     quiet=True, kalman_primary=False)
    elapsed = time.time() - t0

    ta.TREND_TO_BE_OLD_SECONDS = orig_trend_to_be_old
    ta.TrendState.is_trend_consistent_validated = orig_consistent

    import json
    pnl_path = os.path.join(out_dir, "pnl.json")
    pnl = json.load(open(pnl_path)) if os.path.exists(pnl_path) else {}
    # quiet=True -> log.disable_print() monkeypatch-uieste builtins.print GLOBAL
    # (nu doar in tradeall.py) -> print() de mai jos ar fi inghitit tacut. stderr
    # ramane vizibil (acelasi motiv pt care tradeall_backtest.py insusi foloseste
    # sys.stderr.write pt propriile mesaje de progres in modul --quiet).
    sys.stderr.write(f"\n=== {tag} === (wall {elapsed:.1f}s)\n")
    sys.stderr.write(f"stats: {STATS[tag]}\n")
    sys.stderr.write(f"pnl: {pnl}\n")
    return STATS[tag], pnl


if __name__ == "__main__":
    from datetime import datetime, timedelta
    # TAOUSDC, nu BTCUSDC: backtest-ul principal a aratat deja activitate (186 BUY
    # intr-un puseu) pe TAO, in timp ce BTC a stat la 0/0 zeci de mii de tick-uri —
    # avem nevoie de un simbol unde divergenta gradient/slope_big chiar se intampla,
    # ca sa observam starts/confirms/expires, nu doar zerouri plate.
    symbol = "TAOUSDC"
    # arhiva TAO incepe la 2026-07-14 19:35:09 (verificat direct din jsonl) — NU
    # miezul noptii (prima incercare a dat 0 tick-uri, fereastra cadea inainte de
    # inceputul real al datelor).
    start_ts = datetime.strptime("2026-07-14 19:40:00", "%Y-%m-%d %H:%M:%S").timestamp()
    end_ts = start_ts + 12 * 3600   # 12h — acopera puseul de 186 BUY-uri vazut in backtest-ul principal

    run_variant("baseline_8h", symbol, start_ts, end_ts)

    run_variant("loosen_expiration_30min_8h", symbol, start_ts, end_ts,
                expiration_trend_time_override=30 * 60)

    run_variant("loosen_expiration_1h_and_old_20min_8h", symbol, start_ts, end_ts,
                expiration_trend_time_override=60 * 60,
                trend_to_be_old_override=20 * 60)

    run_variant("lower_confirm_threshold_6_8h", symbol, start_ts, end_ts,
                confirm_threshold_override=6)
