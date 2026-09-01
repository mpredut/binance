#!/usr/bin/env python3
"""
monitor_night.py — overnight monitoring for the trading bots.
Runs through Paramiko from WSL and connects to the real server on port 32238.
Output: per-process status, recent log errors, and actions taken.

Credentials come from the gitignored .env at the repository root. Keys read:
  MONITOR_HOST (REQUIRED, no default), MONITOR_PASS (REQUIRED, no default),
  MONITOR_PORT (default 32238), MONITOR_USER (REQUIRED),
  MONITOR_ROOT (REQUIRED remote repository path).

MONITOR_HOST deliberately has no default. A built-in address does not fail when
configuration is missing: it connects to whatever lives at that address today and
reports its processes as if they were the trading server's.
"""
import os
import sys
import collections
import datetime
from pathlib import Path

import paramiko
from dotenv import load_dotenv

_REPO = Path(__file__).resolve().parent.parent   # verify_tools/ -> repository root
load_dotenv(_REPO / ".env")                      # Secrets (gitignored).

HOST = os.environ.get("MONITOR_HOST")  # no default: an address must be configured
PORT = int(os.environ.get("MONITOR_PORT", "32238"))
USER = os.environ.get("MONITOR_USER")
PASS = os.environ.get("MONITOR_PASS")  # No default: the secret DOES NOT live in code.
ROOT = os.environ.get("MONITOR_ROOT")

# The process list is no longer hard-coded here. It is read live from the server's
# procs.conf, the same single source of truth used by bots_start, flota_start, and
# healthcheck. See load_procs().
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
    """Return error lines found within the last 30 log lines."""
    if not log_path:
        return []
    out = run(c, f"tail -30 {ROOT}/{log_path} 2>/dev/null")
    if not out or out == "(timeout)":
        return []
    errors = []
    for line in out.splitlines():
        if any(kw in line for kw in ERROR_KEYWORDS):
            errors.append(line.strip())
    return errors[-5:]  # At most five relevant lines.


def _rel_dir(dir_field):
    """'$ROOT/212trading' -> '212trading'; '$ROOT' -> ''."""
    d = dir_field.replace("$ROOT", "").lstrip("/")
    return d


def _log_from_cmd(start_cmd):
    """Extract the log file from the startup command's '>> x.log' redirection."""
    if ">>" not in start_cmd:
        return ""
    after = start_cmd.split(">>", 1)[1].strip()
    return after.split()[0] if after else ""


def load_procs(c):
    """Read procs.conf from the server and derive the log to scan for each process.

    Format procs.conf:  pat | dir | start_cmd | label | hb_log | hb_stale_s | role
    Log path (relative to ROOT):
      - fleet: flota_start writes logs/<script>.log (script comes from pat).
      - bot:   hb_log when present, otherwise the '>> x.log' redirection from
               start_cmd; both are relative to dir.
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
    # Fail loudly on missing configuration instead of falling back to a builtin
    # address: a wrong default connects to the wrong machine and reports its
    # processes as if they were the trading server's.
    missing = [name for name, value in (
        ("MONITOR_HOST", HOST), ("MONITOR_USER", USER),
        ("MONITOR_PASS", PASS), ("MONITOR_ROOT", ROOT),
    )
               if not value]
    if missing:
        print(f"  ERROR: {', '.join(missing)} missing from .env "
              f"(server address / password). Add them to the .env file next to "
              f"monitor_night.py.")
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

    # 1. Process inventory from the server's procs.conf (single source of truth).
    procs = load_procs(c)
    if not procs:
        print("  EROARE: procs.conf gol sau necitibil pe server.")
        c.close()
        return 1

    # 2. Process status through healthcheck --check (read-only).
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
