#!/usr/bin/env python3
"""
monitor_night.py — monitorizare de noapte a botilor de trading.
Ruleaza via paramiko din WSL; se conecteaza la serverul real (portul 32238).
Output: status per proces, erori recente din loguri, actiuni luate.

Credentiale: din .env (gitignored) din radacina repo. Chei citite:
  MONITOR_HOST (default 192.168.0.144), MONITOR_PORT (default 32238),
  MONITOR_USER (default predut), MONITOR_PASS (OBLIGATORIU, fara default).
"""
import os
import sys
import collections
import datetime
from pathlib import Path

import paramiko
from dotenv import load_dotenv

_REPO = Path(__file__).resolve().parent.parent   # verify_tools/ -> radacina repo
load_dotenv(_REPO / ".env")                      # secrete (gitignored)

HOST = os.environ.get("MONITOR_HOST", "192.168.0.144")
PORT = int(os.environ.get("MONITOR_PORT", "32238"))
USER = os.environ.get("MONITOR_USER", "predut")
PASS = os.environ.get("MONITOR_PASS")  # fara default: secretul NU sta in cod
ROOT = os.environ.get("MONITOR_ROOT", "/home/predut/binance")

# Lista de procese NU mai e hardcodata aici: se citeste live din procs.conf de pe
# server (sursa unica de adevar, aceeasi pe care o folosesc bots_start/flota_start/
# healthcheck). Vezi load_procs().
Proc = collections.namedtuple("Proc", "pat label log role")

ERROR_KEYWORDS = ["Traceback", "Exception", "ERROR", "FAIL", "crash", "hung",
                  "ConnectionRefused", "OSError", "TimeoutError", "CRITICAL"]


def run(c, cmd, wait=20):
    _, out, err = c.exec_command(cmd)
    out.channel.settimeout(wait)
    try:
        o = out.read().decode(errors="replace").strip()
    except Exception:
        o = "(timeout)"
    return o


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASS, timeout=15)
    return c


def check_errors(c, log_path):
    """Returneaza liniile de eroare din ultimele 30 de linii de log."""
    if not log_path:
        return []
    out = run(c, f"tail -30 {ROOT}/{log_path} 2>/dev/null")
    if not out or out == "(timeout)":
        return []
    errors = []
    for line in out.splitlines():
        if any(kw in line for kw in ERROR_KEYWORDS):
            errors.append(line.strip())
    return errors[-5:]  # max 5 linii relevante


def _rel_dir(dir_field):
    """'$ROOT/212trading' -> '212trading'; '$ROOT' -> ''."""
    d = dir_field.replace("$ROOT", "").lstrip("/")
    return d


def _log_from_cmd(start_cmd):
    """Extrage fisierul de log din redirectul '>> x.log' al comenzii de pornire."""
    if ">>" not in start_cmd:
        return ""
    after = start_cmd.split(">>", 1)[1].strip()
    return after.split()[0] if after else ""


def load_procs(c):
    """Citeste procs.conf de pe server si deriva pentru fiecare proces logul de scanat.

    Format procs.conf:  pat | dir | start_cmd | label | hb_log | hb_stale_s | role
    Logul (relativ la ROOT):
      - fleet: flota_start scrie in logs/<script>.log (script = din pat).
      - bot:   hb_log daca exista, altfel redirectul '>> x.log' din start_cmd,
               ambele relative la 'dir'.
    """
    raw = run(c, f"cat {ROOT}/procs.conf 2>/dev/null")
    procs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        pat, dir_field, start_cmd, label, hb_log, _stale, role = (p.strip() for p in parts[:7])
        reldir = _rel_dir(dir_field)

        if role == "fleet":
            script = os.path.basename(pat.split()[0])          # 'cacheManager.py'
            log = f"logs/{script[:-3] if script.endswith('.py') else script}.log"
        else:
            rel = hb_log or _log_from_cmd(start_cmd)
            log = f"{reldir}/{rel}" if (reldir and rel) else rel

        procs.append(Proc(pat=pat, label=label, log=log, role=role))
    return procs


def main():
    if not PASS:
        print("  EROARE: MONITOR_PASS lipseste din .env (parola serverului). "
              "Adauga MONITOR_PASS=... in .env langa monitor_night.py.")
        return 2

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  MONITORING CHECK  {ts}")
    print(f"{'='*60}")

    try:
        c = connect()
    except Exception as e:
        print(f"  EROARE CRITICA: nu pot conecta la server: {e}")
        return 1

    # 1. Inventarul de procese: din procs.conf de pe server (sursa unica de adevar)
    procs = load_procs(c)
    if not procs:
        print("  EROARE: procs.conf gol sau necitibil pe server.")
        c.close()
        return 1

    # 2. Status procese via healthcheck --check (read-only)
    check_out = run(c, f"cd {ROOT} && bash healthcheck.sh --check 2>&1", wait=30)
    print("\n--- healthcheck --check ---")
    print(check_out)

    # 3. Analiza per-proces
    mort = []          # [(label, role)]
    warn = []
    ok = []

    for p in procs:
        alive = run(c, f"pgrep -f '{p.pat}' > /dev/null 2>&1 && echo YES || echo NO")
        is_alive = alive.strip() == "YES"
        errors = check_errors(c, p.log)

        if not is_alive:
            mort.append((p.label, p.role))
            print(f"\n  ❌ {p.label}: MORT")
        elif errors:
            warn.append((p.label, errors))
            print(f"\n  ⚠  {p.label}: OK dar cu erori recente")
            for e in errors:
                print(f"       {e}")
        else:
            ok.append(p.label)
            print(f"  ✅ {p.label}: OK")

    # 4. Restart procese moarte (role=bot) via healthcheck --supervise
    actions = []
    if mort:
        bot_mort = [l for l, r in mort if r == "bot"]
        fleet_mort = [l for l, r in mort if r == "fleet"]

        if bot_mort:
            print(f"\n--- restart boti morti: {bot_mort} ---")
            sup_out = run(c, f"cd {ROOT} && bash healthcheck.sh --supervise 2>&1", wait=30)
            print(sup_out)
            actions.append(f"RESTART: {bot_mort}")

        if fleet_mort:
            print(f"\n  ⚠  FLEET mort (nu repornesc manual, tine flota_start): {fleet_mort}")
            actions.append(f"ALERT-FLEET: {fleet_mort}")

    # 5. Rezumat final
    print(f"\n{'='*60}")
    print(f"  REZUMAT: OK={ok}  MORT={[l for l,_ in mort]}  WARN={[l for l,_ in warn]}")
    if actions:
        print(f"  ACTIUNI: {actions}")
    else:
        print(f"  ACTIUNI: nicio interventie necesara")
    print(f"{'='*60}\n")

    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
