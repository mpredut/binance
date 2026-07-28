#!/usr/bin/env python3
"""apply_proposals.py — PROD: trage propunerile de backtest de pe branch-ul git
`backtest-proposals` si le aplica cu guardrail-uri. NU reruleaza backtestul (dev
a facut-o deja). Restartul procesului proprietar NU se face aici — il face
watchdogfor_cacheandconfig cand detecteaza schimbarea de config (decizie user).

Guardrail-uri (aplicate AICI, pe prod, unde valoarea live e autoritativa):
  - valoarea curenta se RECITESTE live de pe prod (nu se ia din propunere) — daca
    prod s-a schimbat intre timp, aplicam fata de valoarea reala;
  - MEDIE, nu salt: new = (current_prod + winner) / 2 (amortizare, ca in pilot);
  - RATE-LIMIT: acelasi parametru nu se schimba mai des de MIN_DAYS_BETWEEN_CHANGES;
  - AUDIT: fiecare decizie (aplicata sau nu) in logger/backtest_pilot_audit.jsonl;
  - commit git pe main dupa fiecare schimbare (istoric + reversibil).

  --dry-run : arata ce ar aplica, fara sa scrie config / commit
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = "/home/predut/binance"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research", "monitortrades_backtest"))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import scheduled_pilot as sp  # refolosim: _apply_config_change/_current_value/_append_audit/_notify_change/_last_change_for/constante  # noqa: E402

# Incarca .env + config.env ca notificarile (PHONE_ALERT_URL/NTFY_TOPIC din .env) sa
# functioneze — apply ruleaza standalone, nu prin fleet (care le incarca la pornire).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    load_dotenv(os.path.join(ROOT, "config.env"))
except Exception:  # noqa: BLE001
    pass

BRANCH = "backtest-proposals"


def _read_proposals():
    """Citeste backtest_proposals.json de pe origin/BRANCH FARA checkout (git show)."""
    subprocess.run(["git", "-C", ROOT, "fetch", "-q", "origin", BRANCH],
                   capture_output=True, timeout=30)
    out = subprocess.run(["git", "-C", ROOT, "show", f"origin/{BRANCH}:backtest_proposals.json"],
                         capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        return []
    try:
        return json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                     help="arata ce ar aplica, fara sa scrie config/commit")
    args = ap.parse_args()

    if os.environ.get("APPLY_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        print("[apply] APPLY_DISABLED=true -- ies fara sa fac nimic")
        return

    proposals = _read_proposals()
    if not proposals:
        print("[apply] nicio propunere pe branch backtest-proposals — nimic de facut")
        return

    for p in proposals:
        fk, section, key = p["full_key"], p["section"], p["key"]
        symbol, winner = p["symbol"], float(p["winner_value"])
        current = sp._current_value(section, key)   # LIVE de pe prod (autoritativ)
        rec = {"full_key": fk, "symbol": symbol, "source": "apply_proposals",
               "winner_value": winner, "current_value": current, "dev_commit": p.get("dev_commit")}

        if abs(winner - current) < 1e-9:
            rec["action"] = "no_change"
            rec["reason"] = f"castigatorul {winner} = valoarea deja configurata pe prod"
            sp._append_audit(rec)
            print(f"[apply] {fk}: no_change (deja {current})")
            continue

        last = sp._last_change_for(fk)
        if last:
            last_ts = datetime.fromisoformat(last["ts"])
            if datetime.now() - last_ts < timedelta(days=sp.MIN_DAYS_BETWEEN_CHANGES):
                rec["action"] = "rate_limited"
                rec["reason"] = f"schimbat ultima data la {last['ts']} (< {sp.MIN_DAYS_BETWEEN_CHANGES}z)"
                sp._append_audit(rec)
                print(f"[apply] {fk}: rate_limited (ultima schimbare {last['ts']})")
                continue

        new_value = round((current + winner) / 2, 4)
        rec["proposed_new_value"] = new_value
        rec["action"] = "would_apply" if args.dry_run else "applied"
        tag = " [dry-run]" if args.dry_run else ""
        print(f"[apply] {fk}: {current} -> {new_value} (winner backtest {winner}, dev {p.get('dev_commit')}){tag}")

        if not args.dry_run:
            sp._apply_config_change(section, key, current, new_value)
            sp._notify_change(fk, symbol, current, new_value, winner, rec)
            subprocess.run(["git", "-C", ROOT, "add", "instruments.conf"], capture_output=True, timeout=10)
            subprocess.run(["git", "-C", ROOT, "commit", "-q", "-m",
                            f"apply backtest proposal: {fk} {current}->{new_value} "
                            f"(winner {winner}, dev {p.get('dev_commit')})"], capture_output=True, timeout=10)
        sp._append_audit(rec)

    if not args.dry_run:
        print("[apply] gata. Restartul procesului proprietar il face watchdogfor_cacheandconfig "
              "(detecteaza schimbarea instruments.conf).")


if __name__ == "__main__":
    main()
