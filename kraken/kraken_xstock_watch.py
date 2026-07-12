#!/usr/bin/env python3
"""
kraken_xstock_watch.py — watcher pt alocarea xStocks (ex. SPCX) pe Kraken.

Ce face, la fiecare verificare:
  1. BALANTA (privat): detecteaza ORICE activ NOU aparut in cont (alocarea poate
     veni sub orice cod — SPCXx, xSPCX...). Alerta speciala daca se potriveste
     XSTOCK_REGEX, alerta informativa altfel.
  2. PERECHI (public): detecteaza cand o pereche SPCX-like devine tranzactionabila
     prin API -> alerta "LISTAT" + instructiuni de pornire a botului cu adoptare.
  3. NIVELE DE PRET (dupa alocare, daca XSTOCK_ALLOC_PRICE e setat): alerta la
     +XSTOCK_TP_ALERT_PCT% / -XSTOCK_SL_ALERT_PCT% fata de pretul alocarii.
     Pret: perechea Kraken daca e listata, altfel subiacentul de pe Yahoo.

  python3 kraken_xstock_watch.py            # bucla continua
  python3 kraken_xstock_watch.py --once     # o singura verificare (test)
  python3 kraken_xstock_watch.py --status   # arata snapshot-ul curent si iese
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from kraken_common import log, load_dotenv, float_env
from notify import notify
from kraken_client import KrakenClient, KrakenError

_HERE = os.path.dirname(os.path.abspath(__file__))


def _state_file() -> str:
    """Calea starii — configurabila (XSTOCK_STATE_FILE) ca sa poti rula mai multe
    watchere in paralel (alte alocari/active), fiecare cu starea lui."""
    return os.environ.get("XSTOCK_STATE_FILE") or os.path.join(_HERE, "xstock_state.json")


# -- stare -------------------------------------------------------------------
def _load_state() -> dict:
    path = _state_file()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            log(f"  ! nu pot citi starea ({e}) — pornesc curat")
    return {"known_assets": {}, "allocated": None, "pair": None,
            "alerted_pair": False, "alerted_tp": False, "alerted_sl": False,
            "bot_pid": None, "alerted_need_price": False}


def _save_state(st: dict) -> None:
    try:
        with open(_state_file(), "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
    except OSError as e:
        log(f"  ! nu pot salva starea: {e}")


# -- pret subiacent (Yahoo) cat timp perechea nu e pe API ---------------------
def yahoo_last(sym: str) -> float | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (watch)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        res = (data.get("chart", {}).get("result") or [None])[0]
        return (res or {}).get("meta", {}).get("regularMarketPrice")
    except Exception as e:  # noqa: BLE001
        log(f"  ! yahoo {sym}: {e}")
        return None


# -- verificari --------------------------------------------------------------
def check_balance(client: KrakenClient, st: dict, rx: str, desktop: bool) -> None:
    """Activ NOU in cont = posibila alocare. Prima rulare = doar snapshot."""
    try:
        bal = client.balance()
    except KrakenError as e:
        log(f"  ! balanta indisponibila ({e}) — continui doar cu watch-ul public")
        return
    assets = {a: float(q) for a, q in bal.items() if float(q) > 0}
    if not st["known_assets"]:
        st["known_assets"] = assets
        log(f"  snapshot initial balanta: {len(assets)} active ({', '.join(sorted(assets))})")
        return
    for a, q in assets.items():
        if a in st["known_assets"]:
            continue
        if re.search(rx, a, re.I):
            st["allocated"] = {"asset": a, "qty": q, "ts": time.time()}
            log(f"  🎯 ALOCARE DETECTATA: {a} = {q}")
            notify(title=f"🎯 ALOCARE xStock: {a} = {q}",
                   body=f"A aparut {a} in contul Kraken (cantitate {q}). "
                        f"Seteaza XSTOCK_ALLOC_PRICE in config.env pt alerte de nivel "
                        f"si STRAT_ADOPT_COST cand perechea devine tranzactionabila.",
                   source="xstock-watch", desktop=desktop)
        else:
            log(f"  ℹ activ nou in cont: {a} = {q}")
            notify(title=f"ℹ Activ nou in cont Kraken: {a} = {q}",
                   body="Verifica daca e alocarea xStock sub alt cod decat cel asteptat.",
                   source="xstock-watch", desktop=desktop)
    st["known_assets"] = assets


def check_pairs(client: KrakenClient, st: dict, rx: str, desktop: bool,
                quote: str = "") -> None:
    """Perechea devine vizibila pe API-ul public = tranzactionabila programatic.
    Daca mai multe perechi se potrivesc (ex. SPCXx/USD si SPCXx/EUR), o prefera
    pe cea cu valuta de cotare `quote`."""
    try:
        pairs = client.asset_pairs()
    except KrakenError as e:
        log(f"  ! asset_pairs: {e}")
        return
    matches = [(k, v) for k, v in pairs.items()
               if re.search(rx, f"{k} {v.get('wsname') or ''} {v.get('base') or ''}", re.I)]
    if not matches:
        return
    if quote:
        pref = [(k, v) for k, v in matches
                if (v.get("wsname") or k).upper().endswith("/" + quote.upper())]
        if pref:
            matches = pref
    k, v = matches[0]
    name = v.get("wsname") or k
    st["pair"] = k
    if not st["alerted_pair"]:
        st["alerted_pair"] = True
        px = None
        try:
            px = client.last_price(k)
        except KrakenError:
            pass
        log(f"  🚀 PERECHE LISTATA pe API: {name} (pret {px})")
        notify(title=f"🚀 {name} LISTAT pe Kraken API" + (f" @ {px}" if px else ""),
               body=f"Poti porni botul cu adoptarea alocarii:\n"
                    f"STRAT_ADOPT_COST=<pret_alocare> python3 kraken_bot.py --pair {k}",
               source="xstock-watch", price=px, desktop=desktop)


def check_levels(client: KrakenClient, st: dict, alloc_price: float,
                 tp_pct: float, sl_pct: float, yahoo_sym: str, desktop: bool) -> None:
    """Alerta o singura data la +tp% / -sl% fata de pretul alocarii."""
    if not st["allocated"] or alloc_price <= 0:
        return
    price = None
    if st["pair"]:
        try:
            price = client.last_price(st["pair"])
        except KrakenError:
            pass
    if price is None and yahoo_sym:
        price = yahoo_last(yahoo_sym)
    if not price:
        return
    chg = (price - alloc_price) / alloc_price * 100
    log(f"  pret {price} vs alocare {alloc_price} ({chg:+.1f}%)")
    qty = st["allocated"].get("qty", 0)
    if not st["alerted_tp"] and chg >= tp_pct:
        st["alerted_tp"] = True
        notify(title=f"📈 xStock {chg:+.1f}% peste alocare ({price})",
               body=f"Valoare estimata: {qty * price:.0f} (alocat la {alloc_price}). "
                    f"Ia in calcul vanzarea partiala / pornirea botului cu adoptare.",
               source="xstock-watch", price=price, desktop=desktop)
    tp2 = float_env("XSTOCK_TP2_ALERT_PCT") or 0.0   # transa 2 (vanzare manuala in transe)
    if tp2 and not st.get("alerted_tp2") and chg >= tp2:
        st["alerted_tp2"] = True
        notify(title=f"📈📈 TRANSA 2: xStock {chg:+.1f}% ({price})",
               body=f"A doua tinta atinsa — vinde restul. Valoare: {qty * price:.0f}.",
               source="xstock-watch", price=price, desktop=desktop)
    if not st["alerted_sl"] and chg <= -sl_pct:
        st["alerted_sl"] = True
        notify(title=f"📉 xStock {chg:+.1f}% sub alocare ({price})",
               body=f"Valoare estimata: {qty * price:.0f} (alocat la {alloc_price}). "
                    f"Decide: tii (DCA) sau tai pierderea.",
               source="xstock-watch", price=price, desktop=desktop)


# -- pornire automata bot ------------------------------------------------------
BOT_SCRIPT = os.path.join(_HERE, "kraken_bot.py")
BOT_LOG = os.path.join(_HERE, "xstock_bot.log")


def _bot_alive(pid) -> bool:
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        # daca e copilul nostru si a murit, seceram zombie-ul (altfel kill 0 ar minti)
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except (ChildProcessError, OSError):
        pass  # nu e copilul nostru (ex. watcher repornit) — verificam cu kill 0
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def maybe_start_bot(st: dict, alloc_price: float, desktop: bool) -> None:
    """PORNESTE AUTOMAT kraken_bot cu adoptarea alocarii cand sunt indeplinite:
    alocare in cont + pereche listata pe API + pret de alocare cunoscut.
    Idempotent: tine PID-ul in stare; la restart nu dubleaza (verifica daca botul
    traieste); daca botul a murit, il REPORNESTE (watchdog) — strategia isi reia
    propria stare din state-file-ul per pereche, deci nu dubleaza pozitia."""
    if os.environ.get("XSTOCK_AUTOSTART", "true").strip().lower() != "true":
        return
    if not (st["allocated"] and st["pair"]):
        return
    if alloc_price <= 0:
        if not st["alerted_need_price"]:
            st["alerted_need_price"] = True
            notify(title="⚠ xStock: completeaza XSTOCK_ALLOC_PRICE",
                   body=f"Alocarea {st['allocated']['asset']} e in cont si perechea "
                        f"{st['pair']} e listata, dar nu stiu pretul alocarii. "
                        f"Seteaza XSTOCK_ALLOC_PRICE in config.env ca sa pornesc botul automat.",
                   source="xstock-watch", desktop=desktop)
        return
    if _bot_alive(st.get("bot_pid")):
        return
    relaunch = st.get("bot_pid") is not None
    env = dict(os.environ)
    env["STRAT_ADOPT_COST"] = str(alloc_price)
    # parametri reglati pt IPO volatil (suprascriu config.env DOAR pt instanta asta)
    for src, dst in (("XSTOCK_BOT_TP_PCT", "STRAT_TAKEPROFIT_PCT"),
                     ("XSTOCK_BOT_DCA_DROP_PCT", "STRAT_DCA_DROP_PCT"),
                     ("XSTOCK_BOT_SL_PCT", "STRAT_STOP_LOSS_PCT"),
                     ("XSTOCK_BOT_DCA", "STRAT_DCA"),
                     ("XSTOCK_BOT_MAX_BUDGET", "STRAT_MAX_BUDGET"),
                     ("XSTOCK_BOT_CHECK_MINUTES", "STRAT_CHECK_MINUTES")):
        if os.environ.get(src):
            env[dst] = os.environ[src]
    cmd = [sys.executable, BOT_SCRIPT, "--pair", st["pair"]]
    if os.environ.get("XSTOCK_BOT_PAPER", "false").strip().lower() == "true":
        cmd.append("--paper")
    try:
        with open(BOT_LOG, "a", encoding="utf-8") as logf:
            proc = subprocess.Popen(cmd, cwd=_HERE, env=env, stdout=logf,
                                    stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        log(f"  ! nu pot porni botul: {e}")
        return
    st["bot_pid"] = proc.pid
    verb = "REPORNIT (era cazut)" if relaunch else "PORNIT AUTOMAT"
    log(f"  🤖 BOT {verb}: pid {proc.pid}  pair {st['pair']}  adopt @ {alloc_price}  (log: {BOT_LOG})")
    notify(title=f"🤖 BOT {verb} pe {st['pair']} (adopt @ {alloc_price})",
           body=f"kraken_bot gestioneaza alocarea: TP/DCA/stop-loss. pid {proc.pid}, log {BOT_LOG}",
           source="xstock-watch", price=alloc_price, desktop=desktop)


# -- proba end-to-end ----------------------------------------------------------
def run_trial(client: KrakenClient, desktop: bool) -> int:
    """PROBA completa pe API-ul REAL, cu bani ZERO: un activ EXISTENT din cont
    (XSTOCK_TRIAL_ASSET, implicit ADA) e tratat ca alocare noua; perechea lui
    reala e 'listarea'; botul porneste FORTAT pe PAPER; la final watchdog-ul e
    verificat (kill -> repornire) si totul e curatat. Alerte reale cu [PROBA]."""
    global notify
    asset = os.environ.get("XSTOCK_TRIAL_ASSET", "ADA")
    quote = os.environ.get("XSTOCK_QUOTE", "USD")
    os.environ["XSTOCK_BOT_PAPER"] = "true"                      # bani ZERO, garantat
    os.environ["XSTOCK_AUTOSTART"] = "true"
    os.environ["XSTOCK_STATE_FILE"] = os.path.join(_HERE, "xstock_state_trial.json")
    if os.path.exists(_state_file()):
        os.remove(_state_file())
    orig_notify = notify
    notify = lambda **kw: orig_notify(**{**kw, "title": "[PROBA] " + kw.get("title", "")})
    verdict = {}
    bot_pid = None
    trial_pair = None
    try:
        log("=== PROBA END-TO-END (bot PAPER, stare izolata, cont real) ===")
        try:
            bal = client.balance()
        except KrakenError as e:
            log(f"  ! nu pot citi balanta: {e}")
            return 1
        if float(bal.get(asset, 0) or 0) <= 0:
            log(f"  ! n-ai {asset} in cont — alege alt activ: XSTOCK_TRIAL_ASSET=...")
            return 1
        st = _load_state()
        st["known_assets"] = {a: float(q) for a, q in bal.items()
                              if float(q) > 0 and a != asset}
        log(f"  [proba] cobai: {asset} — il scot din snapshot ca sa 'soseasca' acum")
        check_balance(client, st, asset, desktop)                # 1. detectie alocare
        verdict["alocare detectata"] = bool(st["allocated"])
        check_pairs(client, st, asset, desktop, quote)           # 2. pereche listata
        verdict["pereche gasita"] = bool(st["pair"])
        trial_pair = st["pair"]
        alloc = client.last_price(st["pair"]) if st["pair"] else None
        if not alloc:
            log("  ! fara pret pt pereche — proba esuata")
            return 1
        log(f"  [proba] pret de alocare simulat: {alloc} (pretul curent)")
        maybe_start_bot(st, alloc, desktop)                      # 3. bot pornit PAPER
        bot_pid = st.get("bot_pid")
        verdict["bot pornit (PAPER)"] = _bot_alive(bot_pid)
        _save_state(st)
        log("  [proba] astept 12s sa adopte pozitia si sa puna TP-ul paper...")
        time.sleep(12)
        os.kill(int(bot_pid), 15)                                # 4. watchdog
        time.sleep(1.0)
        verdict["moartea botului detectata"] = not _bot_alive(bot_pid)
        maybe_start_bot(st, alloc, desktop)
        bot_pid = st.get("bot_pid")
        verdict["bot REPORNIT de watchdog"] = _bot_alive(bot_pid)
        try:
            with open(BOT_LOG, encoding="utf-8") as f:
                tail = [ln.rstrip() for ln in f.readlines()[-14:]]
            log("  [proba] log-ul botului (ce a facut cu 'alocarea'):")
            for ln in tail:
                print("      " + ln)
        except OSError:
            pass
    finally:
        notify = orig_notify
        if bot_pid:
            try:
                os.kill(int(bot_pid), 15)
                time.sleep(0.5)
                _bot_alive(bot_pid)                              # seceram zombie-ul
            except (OSError, TypeError, ValueError):
                pass
        if trial_pair:                                           # stergem starea PAPER a botului
            from strategy import state_path_for
            sp = state_path_for(trial_pair)
            if os.path.exists(sp):
                os.remove(sp)
        if os.path.exists(_state_file()):
            os.remove(_state_file())
    ok = all(verdict.values()) and len(verdict) == 5
    log("=== VERDICT PROBA ===")
    for k, v in verdict.items():
        log(f"    {'✅' if v else '❌'} {k}")
    log(f"=== PROBA {'REUSITA — lantul intreg functioneaza' if ok else 'ESUATA — vezi mai sus'} ===")
    return 0 if ok else 1


def main() -> int:
    load_dotenv(os.path.join(_HERE, ".env"))
    load_dotenv(os.path.join(_HERE, "config.env"))

    ap = argparse.ArgumentParser(description="Watcher alocare xStocks (Kraken).")
    ap.add_argument("--once", action="store_true", help="o singura verificare si iese")
    ap.add_argument("--status", action="store_true", help="arata starea si iese")
    ap.add_argument("--trial", action="store_true",
                    help="PROBA end-to-end cu bani ZERO: activ existent ca alocare simulata, bot PAPER, watchdog testat, curatenie la final")
    ap.add_argument("--desktop", action="store_true")
    ap.add_argument("--interval", type=float,
                    default=float_env("XSTOCK_CHECK_MINUTES") or 10.0, help="minute")
    args = ap.parse_args()

    rx = os.environ.get("XSTOCK_REGEX", "SPCX|SPACEX")
    quote = os.environ.get("XSTOCK_QUOTE", "USD")
    alloc_price = float_env("XSTOCK_ALLOC_PRICE") or 0.0
    tp_pct = float_env("XSTOCK_TP_ALERT_PCT") or 20.0
    sl_pct = float_env("XSTOCK_SL_ALERT_PCT") or 15.0
    yahoo_sym = os.environ.get("XSTOCK_YAHOO", "SPCX")

    client = KrakenClient(os.environ.get("KRAKEN_API_KEY_BOT"), os.environ.get("KRAKEN_API_SECRET_BOT"))
    if args.trial:
        return run_trial(client, args.desktop)
    st = _load_state()

    if args.status:
        print(f"regex={rx}  alloc_price={alloc_price}  tp={tp_pct}%  sl={sl_pct}%  yahoo={yahoo_sym}")
        print(f"active cunoscute: {len(st['known_assets'])} -> {', '.join(sorted(st['known_assets'])) or '-'}")
        print(f"alocare: {st['allocated'] or 'nedetectata'}")
        print(f"pereche API: {st['pair'] or 'nelistata'}")
        alive = _bot_alive(st.get("bot_pid"))
        print(f"bot: {'RULEAZA pid ' + str(st['bot_pid']) if alive else ('cazut (pid ' + str(st['bot_pid']) + ', va fi repornit)' if st.get('bot_pid') else 'nepornit')}")
        return 0

    log("=== xStock watcher pornit ===")
    log(f"    regex      : {rx}")
    log(f"    alocare    : {alloc_price if alloc_price > 0 else 'pret necunoscut (doar detectie)'}")
    log(f"    alerte     : +{tp_pct}% / -{sl_pct}%  (pret: Kraken sau Yahoo {yahoo_sym})")
    log(f"    interval   : {args.interval} min")
    beats = 0
    while True:
        try:
            check_balance(client, st, rx, args.desktop)
            check_pairs(client, st, rx, args.desktop, quote)
            check_levels(client, st, alloc_price, tp_pct, sl_pct, yahoo_sym, args.desktop)
            maybe_start_bot(st, alloc_price, args.desktop)
            _save_state(st)
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001 — REZILIENTA: net picat/DNS -> reincerc, nu mor
            log(f"  ! ciclu esuat ({e.__class__.__name__}: {e}) — reincerc la urmatorul")
        if args.once:
            return 0
        beats += 1                       # puls keep-alive: un punct pe ciclu, vizibil in tail -f
        sys.stdout.write("." if beats % 60 else ".\n")
        sys.stdout.flush()
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    sys.exit(main())
