#!/usr/bin/env python3
"""log_anomaly_watchdog.py — detects ANOMALIES in the bot logs (Binance/Kraken/HL/
T212) and alarms when the error RATE crosses a threshold. Complements cache_watchdog:
that one checks cache freshness, this one checks the *error signals* in the logs
(429, auth, 'blind flying', tracebacks, stale) — the bugs we made observable.

Runs as a short cron task (every 5 min). It reads ONLY the NEW lines of each log
(offset persisted in state, logrotate-aware) -> a natural 'since the last run' window,
with no timestamp parsing. It alerts (ntfy+email) per category, with a cooldown.

Env (from .env / config.env in the repository root):
  ANOMALY_WINDOW_FILES_MIN   — scan only logs touched in the last X minutes
  ANOMALY_COOLDOWN_MINUTES   — do not re-alarm the same category sooner
  ANOMALY_THRESH_<CAT>       — mandatory per-category threshold
"""
import os
import re
import sys
import time
import glob
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog_common as wc

ROOT = wc.ROOT
STATE_FILE = ROOT / ".anomaly_watchdog_state.json"
wc.load_env()

WINDOW_FILES_MIN = wc.required_float_env("ANOMALY_WINDOW_FILES_MIN")
COOLDOWN_MIN = wc.required_float_env("ANOMALY_COOLDOWN_MINUTES")

# Anomaly categories: (case-insensitive regex, default threshold of hits per window).
# The threshold is how many NEW hits (since the last run) trigger the alert.
_CATS = {
    "rate_limit": (re.compile(r"\b429\b|rate limit|too ?many ?requests", re.I), 30),
    # Bilingual ON PURPOSE, and it stays that way (owner's decision). The code is fully
    # English, but a future log line could be written in Romanian by mistake — and an
    # English-only pattern would then stop firing SILENTLY, which is the one failure mode
    # a watchdog must not have. The Romanian alternatives cost one regex branch; missing a
    # real auth failure or a blind-flying window costs money.
    "auth":       (re.compile(r"auth esuat|auth failed|lipsesc cheile|missing keys|"
                             r"unauthorized|forbidden|"
                             r"http\s*40[13]\b|status[=\s]*40[13]\b|\(40[13]\)|invalid.*api.*key", re.I), 3),
    "blind":      (re.compile(r"indisponibil|unavailable|sar reconcilierea|"
                             r"skipping reconciliation|zbor orb|blind flying", re.I), 25),
    "execution_blocked": (re.compile(r"\bweight_policy_unavailable\b", re.I), 25),
    "traceback":  (re.compile(r"traceback \(most recent|unhandledexception|\bfatal\b", re.I), 1),
}
# The regexes are deliberately SPECIFIC (calibrated on real logs):
#  - no generic 'error' category -> on huge logs (rtrade, hundreds of MB/day) it would
#    produce thousands of benign matches (retries) -> a false alarm.
#  - 'auth' needs HTTP context (not bare 40[13], which matched prices/quantities '401').
#  - no 'stale' category: staleness belongs to watchdogfor_cache (it would double the
#    alert; 'portfolio stale' from strategy.py is benign, not an anomaly).

# Log files to scan (only the recently touched ones -> active).
_LOG_GLOBS = ["logger/*.log", "logs/*.log", "212trading/*.log", "hyperliquid/*.log", "kraken/*.log"]
# Byte cap per file per run: the big logs (rtrade) grow by MB/min; without a cap, a
# huge gap would load hundreds of MB into RAM (OOM). We only read the tail.
_MAX_READ_BYTES = wc.required_int_env("ANOMALY_MAX_READ_BYTES")


# DEV/backtest logs: tracebacks here come from the test machine (backtest pilot on
# runner.py, dev sync) — NOT LIVE fleet problems. Excluded from the scan so they do
# not falsely alert "check the affected bots" for dev failures.
# This also covers the test logs (unittest/pytest via runner.py, e.g. "python -m
# unittest_<date>.log"): a failing test in development is NOT an affected bot.
_EXCLUDE_BASENAMES = {
    "backtest_cycle.log", "refresh_dev.log", "trigger_backtest_dev.log",
    "anomaly_watchdog.log", "watchdogfor_anomaly.log",
}


def _is_dev_log(path):
    b = os.path.basename(path)
    # "-c_<date>.log" / "-m_<date>.log": ad-hoc `python -c` / `python -m` one-liner
    # invocations (diagnostics, probes) tagged [unknown]. They are not supervised
    # bots — a traceback from importing a module without its config is expected and
    # must not raise a fleet alert. Real bot errors go to that bot's own log.
    return (b in _EXCLUDE_BASENAMES or b.startswith("backtest")
            # A watchdog's quoted examples must not feed back into its own scan.
            or b.startswith("watchdogfor_anomaly_")
            or "unittest" in b or "pytest" in b
            or b.startswith("-c_") or b.startswith("-m_"))


def _active_logs():
    now = time.time()
    out = []
    for g in _LOG_GLOBS:
        for p in glob.glob(str(ROOT / g)):
            if _is_dev_log(p):
                continue
            try:
                if (now - os.path.getmtime(p)) <= WINDOW_FILES_MIN * 60:
                    out.append(p)
            except OSError:
                pass
    return sorted(set(out))


def _new_lines(path, offsets):
    """The lines that appeared since the last run (persisted offset). Handles logrotate:
    if the file shrank (rotated/truncated), start over from 0. The first time we see a
    file we baseline it at EOF (we do not count its HISTORY -> no false alert on the
    first run or on a new daily log). Return None when the file cannot be read."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if path not in offsets:              # New file -> baseline at EOF, skip the history.
        offsets[path] = size
        return []
    last = offsets[path]
    if size < last:                      # Rotated/truncated: restart at the beginning.
        last = 0
    if size == last:
        return []
    start = last if (size - last) <= _MAX_READ_BYTES else size - _MAX_READ_BYTES  # RAM bound.
    try:
        with open(path, "r", errors="replace") as f:
            f.seek(start)
            data = f.read(_MAX_READ_BYTES)
    except OSError:
        return None
    offsets[path] = size
    return data.splitlines()


def _count_events(paths, offsets):
    """Count new signals, not their stdout/daily/outcome mirror copies.

    Weight-policy refusals use the structured outcome stream when it is already
    baselined and readable. Worker text remains a fallback if that stream is
    absent/new/unreadable. Other categories retain all bot streams. Exact mirror
    lines use the maximum per-file multiplicity, preserving repeated events in
    one file and identical messages from unrelated bots.
    """
    def is_outcomes(path):
        return bool(re.fullmatch(r"order_outcomes_\d{4}-\d{2}-\d{2}\.log",
                                 os.path.basename(path)))

    known = set(offsets)
    outcome_chunks = {p: _new_lines(p, offsets) for p in paths if is_outcomes(p)}
    canonical = bool(outcome_chunks) and all(
        p in known and lines is not None for p, lines in outcome_chunks.items())
    merged = Counter()
    fallback_outcomes = Counter()
    fallback_files = set()
    fallback_sample = None

    # Count hits per category, keeping one sample and the files involved.
    counts = {c: 0 for c in _CATS}
    samples = {}
    files_hit = {c: set() for c in _CATS}
    scanned = 0
    for path in paths:
        per_file = Counter()
        basename = os.path.basename(path)
        family = re.sub(r"_\d{4}-\d{2}-\d{2}(?=\.log$)", "", basename)
        chunk = outcome_chunks[path] if is_outcomes(path) else _new_lines(path, offsets)
        for line in chunk or []:
            scanned += 1
            blocked = _CATS["execution_blocked"][0].search(line)
            for cat, (rx, _thr) in _CATS.items():
                if blocked and cat == "blind":
                    continue  # A pre-submit refusal is not trading without safety data.
                if cat == "execution_blocked" and canonical and not is_outcomes(path):
                    continue
                if rx.search(line):
                    if cat == "execution_blocked" and not canonical and is_outcomes(path):
                        # At rollover/read failure prefer mirrored text, if present,
                        # rather than counting the partially available outcome stream twice.
                        fallback_outcomes[(cat, family, line.strip())] += 1
                        fallback_files.add(basename)
                        fallback_sample = fallback_sample or line.strip()[:200]
                        continue
                    per_file[(cat, family, line.strip().lstrip("\a"))] += 1
                    files_hit[cat].add(basename)
                    samples.setdefault(cat, line.strip()[:200])
        merged |= per_file
    if not canonical and not files_hit["execution_blocked"]:
        merged |= fallback_outcomes
        files_hit["execution_blocked"] = fallback_files
        if fallback_sample is not None:
            samples["execution_blocked"] = fallback_sample
    for (cat, _family, _line), count in merged.items():
        counts[cat] += count
    return counts, samples, files_hit, scanned


def check_once(now=None):
    now = now if now is not None else time.time()
    state = wc.load_state(STATE_FILE)
    offsets = state.get("offsets", {})
    cooldowns = state.get("cooldowns", {})
    counts, samples, files_hit, scanned = _count_events(_active_logs(), offsets)

    state["offsets"] = offsets
    state["last_run"] = now

    # Decide the alerts (over threshold and not in cooldown).
    fired = []
    for cat, (rx, default_thr) in _CATS.items():
        thr = wc.required_float_env(f"ANOMALY_THRESH_{cat.upper()}")
        if counts[cat] < thr:
            continue
        last_alert = cooldowns.get(cat, 0)
        if (now - last_alert) < COOLDOWN_MIN * 60:
            print(f"[anomaly] {cat}={counts[cat]} (threshold {thr:.0f}) but in cooldown — not re-alarming")
            continue
        fired.append(cat)
        cooldowns[cat] = now

    state["cooldowns"] = cooldowns

    if not fired:
        wc.save_state(STATE_FILE, state)
        print(f"[anomaly] OK — {scanned} new lines scanned; "
              + ", ".join(f"{c}={counts[c]}" for c in _CATS))
        return False

    lines = []
    for cat in fired:
        thr = wc.required_float_env(f"ANOMALY_THRESH_{cat.upper()}")
        label = ("execution blocked (pre-submit: weight policy unavailable)"
                 if cat == "execution_blocked" else cat)
        lines.append(f"  • {label}: {counts[cat]} occurrences (threshold {thr:.0f}) in "
                     f"{', '.join(sorted(files_hit[cat])) or '?'}")
        if cat in samples:
            lines.append(f"      ex: {samples[cat]}")
    title = "⚠️ Log anomaly threshold exceeded"
    message = ("The error rate has been above the threshold since the last check:\n" + "\n".join(lines)
               + "\nCheck the affected bots.")
    print(f"[anomaly] ALERT:\n{message}")
    wc.alert(title, message)
    wc.save_state(STATE_FILE, state)
    return True


if __name__ == "__main__":
    sys.exit(0 if not check_once() else 0)
