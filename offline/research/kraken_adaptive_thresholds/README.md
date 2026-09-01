# Investigation: adaptive (volatility) thresholds on Kraken — worth moving from shadow to live? (22-23 Jul 2026)

The question: since 17 July, `kraken/strategy.py` already computes volatility-based adaptive
thresholds for **re-entry** (`SHADOW_K_REENTRY`) and **DCA** (`SHADOW_K_DCA`), but ONLY as a
comparative log line ("[SHADOW] fixed threshold X vs adaptive Y") — never used for the real
decision, which stays on the fixed percentage from `config.env`. Are they worth promoting to
real decisions?

**Nothing in the production code (`kraken/strategy.py`, `config.env`) was modified** — both
scripts below are isolated tests that only read public data (the Kraken OHLC API) and run a
separate simulation.

## An important methodological discovery

Neither `kraken/backtest.py` nor `kraken/backtest_adaptive.py` (tools that already existed in
the repository) models the re-entry barrier
(`STRAT_REENTRY_DROP_PCT`/`STRAT_REENTRY_TOLERANCE_PCT`) — once a position closes (take-profit
or stop-loss), the original simulators re-enter IMMEDIATELY on the next bar, with no waiting.
The REAL strategy explicitly waits for the price to fall below
`last_sell_price*(1-reentry_pct%)` before re-entering. `verify_adaptive_reentry.py` adds that
missing mechanism (faithful to the `botcore.diff_percent`/`are_close` formula for the tolerance).

## Results (HYPEUSD, 60 hourly bars, ~30 days — the limit of Kraken's public API for historical
data; the parameters are the REAL values from `kraken/config.env`, not the original scripts'
defaults — the mistake caught in an earlier session was precisely using guessed parameters,
which inverted the conclusion)

### 1. The DCA threshold (`verify_adaptive_dca.py`) — K_DCA × vol_1h vs STRAT_DCA_DROP_PCT=1.0%

| variant | mean threshold | TOTAL | realised | cycles | win rate | maxDD |
|---|---|---|---|---|---|---|
| FIXED (live today) | 1.0% | **+1.90%** | +$164.45 | 6 | 83% | $175.30 |
| adaptive shadow | 0.76% (0.36-2.29%) | +0.44% | +$128.56 | 7 | 86% | $251.84 |

**The fixed threshold wins clearly** — the adaptive threshold, lower on average (so more
permissive about DCA), led to a final position 50% larger (32.3 versus 21.4 units) and a much
larger drawdown, without a compensating improvement in return.

### 2. The re-entry threshold (`verify_adaptive_reentry.py`) — K_REENTRY × vol_1h vs STRAT_REENTRY_DROP_PCT=2.2%

**The adaptive threshold wins clearly, on every criterion** — a higher total return, a higher
realised profit, a better win rate AND a smaller drawdown. The adaptive threshold was more
permissive (~1.5%) during quiet periods (re-entering faster, not missing small recoveries) and
stricter (up to 4.58%) during volatile ones (avoiding a premature re-entry into a false bottom).

## Conclusion

**The two thresholds must NOT be treated the same way** — the user's intuition is confirmed for
RE-ENTRY, but not for DCA:
- **DCA**: stays on the FIXED threshold (1.0%) — the adaptive version was tested and loses.
- **Re-entry**: the adaptive threshold shows a consistent improvement on every criterion (not
  just return, but risk too — a smaller drawdown). It deserves serious consideration for
  promotion to a real decision.

**An honest warning about the sample**: only ~30 days and 6-7 complete trading cycles — far
smaller than the 329-day sample in the `tradeall.py` investigation
(`offline/research/tradeall_trigger_gate/`). A result over so few cycles can be sensitive to
one or two individual trades. Before a real promotion, it would be worth either (a) extending
the test window if a longer data source appears (the account's real trade history, not just
public OHLC), or (b) a gradual, monitored promotion (shadow -> gate, not straight from shadow
to a single decision), following exactly the pattern already used for Kalman on `tradeall.py`
(shadow 17 Jul -> gate 19 Jul -> primary on one symbol only, never a direct leap).

## Files

- `verify_adaptive_dca.py` — the test on the DCA threshold; it reuses the engine from
  `kraken/backtest_adaptive.py` (skipping the Chronos/ML part).
- `verify_adaptive_reentry.py` — the test on the re-entry threshold, with the re-entry barrier
  added (missing from the original tools).

Running them (from the repository root, with `myenv` activated):
```bash
python3 offline/research/kraken_adaptive_thresholds/verify_adaptive_dca.py
python3 offline/research/kraken_adaptive_thresholds/verify_adaptive_reentry.py
```
