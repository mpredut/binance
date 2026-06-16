#!/usr/bin/env python3
"""
t212_bot.py — bot UNIFICAT Trading 212: UN proces, mai multe active.

Inlocuieste `ipo.py --profile X` (cate un proces per activ). Descopera toate
fisierele config.<activ>.env, porneste cate un THREAD pentru fiecare:
  * IZOLAT — eroarea unui activ nu-i omoara pe ceilalti (try/except + reincearca);
  * UN singur client T212 cu throttle comun -> mai putine 429 (rate-limit);
  * config per activ luat din FISIER, nu din linia de comanda.

Adaugi un activ = creezi config.<activ>.env (fara cod nou, fara proces/cron nou).
Scoti un activ = redenumesti fisierul (ex. config.nvda.env.off).

  python3 t212_bot.py                # ruleaza toate config.*.env (REAL daca STRAT_EXECUTE=true)
  python3 t212_bot.py --paper        # forteaza PAPER pe toate (test sigur)
  python3 t212_bot.py --only nvda    # doar un activ (debug)
  python3 t212_bot.py --skip-wait    # sari peste asteptarea lansarii (porneste direct)
  python3 t212_bot.py --list         # arata ce active ar porni, fara sa porneasca
"""
from __future__ import annotations

import argparse
import glob
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ipo_common import load_dotenv, log, parse_dotenv  # noqa: E402
from ipo_notify import notify  # noqa: E402
from listing_watcher import wait_for_launch  # noqa: E402
from strategy import Strategy, StratParams  # noqa: E402
from t212_client import T212Client  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
STOP = threading.Event()


def discover_assets(cfg_dir: str) -> list[tuple[str, str]]:
    """Toate config.<activ>.env din director -> [(nume, cale), ...] sortate.
    config.nvda.env -> ('nvda', '.../config.nvda.env')."""
    out = []
    for p in sorted(glob.glob(os.path.join(cfg_dir, "config.*.env"))):
        name = os.path.basename(p)[len("config."):-len(".env")]
        out.append((name, p))
    return out


def verify_isin(client: T212Client, ticker: str, expected_isin: str) -> bool:
    """False DOAR la nepotrivire dovedita de ISIN (instrument gresit). Altfel True
    (best-effort: daca metadata lipseste, continui pe ticker-ul explicit)."""
    if not expected_isin:
        return True
    instruments = client.list_instruments()
    if not instruments:
        return True
    match = next((i for i in instruments if str(i.get("ticker", "")).upper() == ticker.upper()), None)
    if not match:
        return True
    if str(match.get("isin", "")) != expected_isin:
        log(f"  ! [{ticker}] ISIN {match.get('isin')} != asteptat {expected_isin} — NU tranzactionez")
        notify(title=f"⚠ {ticker}: ISIN nepotrivit", source="verify",
               body=f"Gasit {match.get('isin')}, asteptam {expected_isin}.", symbol=ticker)
        return False
    return True


def run_asset(name: str, cfg: dict, client: T212Client, force_paper: bool, skip_wait: bool) -> None:
    """Ciclul de viata al unui activ, in thread-ul lui. Izolat: prinde orice eroare
    si reincearca (nu omoara procesul / ceilalti boti)."""
    ticker = (cfg.get("T212_TICKER") or "").strip()
    label = cfg.get("SYMBOL_LABEL") or cfg.get("YAHOO_SYMBOL") or ticker or name
    yahoo = (cfg.get("YAHOO_SYMBOL") or "").strip() or (ticker.split("_")[0] if ticker else "")
    isin = (cfg.get("EXPECTED_ISIN") or "").strip()
    strat_enabled = cfg.get("STRAT_ENABLED", "false").strip().lower() == "true"
    strat_dry = force_paper or cfg.get("STRAT_EXECUTE", "false").strip().lower() != "true"
    try:
        interval = max(int(float(cfg.get("POLL_SECONDS") or 60)), 30)
    except ValueError:
        interval = 60

    if not ticker:
        log(f"  ! [{name}] lipseste T212_TICKER — sar peste"); return
    if not strat_enabled:
        log(f"  ! [{name}] STRAT_ENABLED!=true — sar peste (t212_bot ruleaza doar strategii)"); return

    log(f"  ▶ [{label}] {ticker} | pret via {yahoo} | {'PAPER' if strat_dry else '⚠ REAL — BANI'} | poll {interval}s")
    while not STOP.is_set():
        try:
            if not verify_isin(client, ticker, isin):
                return  # config gresit -> nu reincerca orbeste
            if not skip_wait:
                ok = wait_for_launch(
                    yahoo, label, interval, stop=STOP,
                    on_launch=lambda m: notify(
                        title=f"{label} disponibil — pornesc!", source="listing",
                        body=f"{label} tranzactionabil pe {m.get('exchange')} @ {m.get('price')}",
                        price=m.get("price"), symbol=label))
                if not ok:
                    return  # STOP cerut
                if not verify_isin(client, ticker, isin):
                    return
            # blocheaza in bucla proprie a strategiei (self-healing pe erori interne);
            # revine doar la oprire neasteptata -> reincercam de la verificare.
            Strategy(client, ticker, StratParams.from_env(cfg), dry_run=strat_dry).run()
            return
        except Exception as e:  # noqa: BLE001 — REZILIENTA: un activ nu poate dobori procesul
            log(f"  ! [{label}] eroare ciclu ({e.__class__.__name__}: {e}) — reincerc in 60s")
            STOP.wait(60)
    log(f"  ⏹ [{label}] oprit")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bot unificat T212: un proces, mai multe active (config.*.env).")
    ap.add_argument("--paper", action="store_true", help="Forteaza PAPER pe toate (test sigur)")
    ap.add_argument("--only", metavar="NUME", help="Ruleaza doar activul cu acest nume (config.NUME.env)")
    ap.add_argument("--skip-wait", action="store_true", help="Sari peste asteptarea lansarii")
    ap.add_argument("--list", action="store_true", help="Arata activele si iesi")
    ap.add_argument("--env-file", default=os.path.join(_HERE, ".env"))
    args = ap.parse_args()

    # Secrete PARTAJATE din root binance/.env (NTFY/SMTP/etc., comune flotei) + secrete
    # SPECIFICE T212 din folderul propriu (212trading/.env). Specificul se incarca ULTIMUL
    # (prioritate la suprapuneri). Asa cheile T212 stau in folderul lor, nu in root.
    load_dotenv(os.path.join(os.path.dirname(_HERE), ".env"))  # shared (root)
    load_dotenv(args.env_file)                                 # specific (212trading/.env)
    cfg_dir = os.path.dirname(args.env_file) or _HERE
    assets = discover_assets(cfg_dir)
    if args.only:
        assets = [(n, p) for (n, p) in assets if n == args.only]
    if not assets:
        log(f"! niciun config.*.env gasit in {cfg_dir}" + (f" pt '{args.only}'" if args.only else ""))
        return 1

    if args.list:
        log(f"=== {len(assets)} active ===")
        for n, p in assets:
            c = parse_dotenv(p)
            real = c.get("STRAT_EXECUTE", "").strip().lower() == "true" and not args.paper
            on = c.get("STRAT_ENABLED", "").strip().lower() == "true"
            log(f"  {n:<8} {c.get('T212_TICKER','?'):<14} "
                f"{'STRAT' if on else 'OFF':<6} {'⚠ REAL' if real else 'PAPER'}")
        return 0

    key = os.environ.get("T212_API_KEY")
    if not key:
        log("! T212_API_KEY lipsa in .env — nu pot continua"); return 1
    client = T212Client(key, os.environ.get("T212_API_SECRET"),
                        env=os.environ.get("T212_ENV", "live").strip().lower())

    log(f"=== t212_bot: {len(assets)} active intr-UN proces ({'PAPER fortat' if args.paper else 'config'}) ===")
    client.list_instruments()  # incalzeste cache-ul O DATA -> threadurile nu mai fac apelul greu
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    threads = []
    for name, path in assets:
        cfg = parse_dotenv(path)
        t = threading.Thread(target=run_asset, name=name, daemon=True,
                             args=(name, cfg, client, args.paper, args.skip_wait))
        t.start()
        threads.append(t)
        time.sleep(0.5)  # decaleaza pornirile (sa nu loveasca API-ul simultan)

    try:
        while not STOP.is_set() and any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        log("Oprire ceruta (Ctrl+C)...")
        STOP.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
