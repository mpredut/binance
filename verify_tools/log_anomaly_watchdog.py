#!/usr/bin/env python3
"""log_anomaly_watchdog.py — detecteaza ANOMALII in logurile botilor (Binance/Kraken/HL/
T212) si alarmeaza cand RATA de erori depaseste un prag. Complementar cache_watchdog:
acela verifica prospetimea cache-urilor; asta verifica *semnalele de eroare* din loguri
(429, auth, 'zbor orb', tracebacks, stale) — bugurile pe care le-am facut observabile.

Ruleaza ca task scurt din cron (la fiecare 5 min). Citeste DOAR liniile NOI din fiecare log
(offset persistat in state, ca logrotate) -> fereastra naturala 'de la ultima rulare',
fara parsare de timestamp. Alerteaza (ntfy+email) per categorie, cu cooldown.

Env (din .env / config.env din radacina):
  ANOMALY_WINDOW_FILES_MIN   (default 30) — scaneaza doar loguri atinse in ultimele X min
  ANOMALY_COOLDOWN_MINUTES   (default 30) — nu re-alarma aceeasi categorie mai des
  ANOMALY_THRESH_<CAT>       — prag per categorie (vezi _THRESH); override din env
"""
import os
import re
import sys
import time
import glob
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog_common as wc

ROOT = wc.ROOT
STATE_FILE = ROOT / ".anomaly_watchdog_state.json"
wc.load_env()

WINDOW_FILES_MIN = float(os.environ.get("ANOMALY_WINDOW_FILES_MIN", "30"))
COOLDOWN_MIN = float(os.environ.get("ANOMALY_COOLDOWN_MINUTES", "30"))

# Categorii de anomalii: (regex case-insensitive, prag implicit de aparitii/fereastra).
# Pragul e cat de multe aparitii NOI (de la ultima rulare) declanseaza alerta.
_CATS = {
    "rate_limit": (re.compile(r"429|rate limit|too ?many ?requests", re.I), 30),
    "auth":       (re.compile(r"auth esuat|lipsesc cheile|\b40[13]\b|invalid.*key", re.I), 3),
    "blind":      (re.compile(r"indisponibil|sar reconcilierea|zbor orb", re.I), 25),
    "traceback":  (re.compile(r"traceback \(most recent|unhandled|fatal", re.I), 1),
    "error":      (re.compile(r"\besuat\b|\beroare\b|\bexception\b", re.I), 40),
    "stale":      (re.compile(r"\bstale\b|invechit|cache.*mort", re.I), 3),
}

# Fisiere de log de scanat (doar cele atinse recent -> active).
_LOG_GLOBS = ["logger/*.log", "logs/*.log", "212trading/*.log", "hyperliquid/*.log", "kraken/*.log"]


def _active_logs():
    now = time.time()
    out = []
    for g in _LOG_GLOBS:
        for p in glob.glob(str(ROOT / g)):
            try:
                if (now - os.path.getmtime(p)) <= WINDOW_FILES_MIN * 60:
                    out.append(p)
            except OSError:
                pass
    return sorted(set(out))


def _new_lines(path, offsets):
    """Liniile aparute de la ultima rulare (offset persistat). Gestioneaza logrotate:
    daca fisierul s-a micsorat (rotit/truncat), reia de la 0. Prima data cand vedem un
    fisier, il baseline-uim la EOF (nu-i numaram ISTORICUL -> fara alerta falsa la primul
    run / la un log nou de zi)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if path not in offsets:              # fisier nou -> baseline la EOF, fara istoricul
        offsets[path] = size
        return []
    last = offsets[path]
    if size < last:                      # rotit/truncat -> reia de la inceput
        last = 0
    if size == last:
        return []
    try:
        with open(path, "r", errors="replace") as f:
            f.seek(last)
            data = f.read()
    except OSError:
        return []
    offsets[path] = size
    return data.splitlines()


def check_once(now=None):
    now = now if now is not None else time.time()
    state = wc.load_state(STATE_FILE)
    offsets = state.get("offsets", {})
    cooldowns = state.get("cooldowns", {})

    # numara aparitii per categorie + retine un exemplu si fisierele implicate
    counts = {c: 0 for c in _CATS}
    samples = {}
    files_hit = {c: set() for c in _CATS}
    scanned = 0
    for path in _active_logs():
        for line in _new_lines(path, offsets):
            scanned += 1
            for cat, (rx, _thr) in _CATS.items():
                if rx.search(line):
                    counts[cat] += 1
                    files_hit[cat].add(os.path.basename(path))
                    samples.setdefault(cat, line.strip()[:200])

    state["offsets"] = offsets
    state["last_run"] = now

    # decide alertele (peste prag + nu in cooldown)
    fired = []
    for cat, (rx, default_thr) in _CATS.items():
        thr = float(os.environ.get(f"ANOMALY_THRESH_{cat.upper()}", default_thr))
        if counts[cat] < thr:
            continue
        last_alert = cooldowns.get(cat, 0)
        if (now - last_alert) < COOLDOWN_MIN * 60:
            print(f"[anomaly] {cat}={counts[cat]} (prag {thr:.0f}) dar in cooldown — nu re-alarmez")
            continue
        fired.append(cat)
        cooldowns[cat] = now

    state["cooldowns"] = cooldowns

    if not fired:
        wc.save_state(STATE_FILE, state)
        print(f"[anomaly] OK — {scanned} linii noi scanate; "
              + ", ".join(f"{c}={counts[c]}" for c in _CATS))
        return False

    lines = []
    for cat in fired:
        thr = float(os.environ.get(f"ANOMALY_THRESH_{cat.upper()}", _CATS[cat][1]))
        lines.append(f"  • {cat}: {counts[cat]} aparitii (prag {thr:.0f}) in "
                     f"{', '.join(sorted(files_hit[cat])) or '?'}")
        if cat in samples:
            lines.append(f"      ex: {samples[cat]}")
    title = "⚠️ Anomalii in loguri (rata erori)"
    message = ("Rata de erori peste prag de la ultima verificare:\n" + "\n".join(lines)
               + "\nVerifica botii afectati.")
    print(f"[anomaly] ALARMA:\n{message}")
    wc.alert(title, message)
    wc.save_state(STATE_FILE, state)
    return True


if __name__ == "__main__":
    sys.exit(0 if not check_once() else 0)
