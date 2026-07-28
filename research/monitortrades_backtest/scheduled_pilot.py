#!/usr/bin/env python3
"""
scheduled_pilot.py — PILOT (un singur modul: monitortrades.py, decizie user
23 iul: "deocamdata facem un test pilot si nu extindem la toate modulele").

Citeste rangurile de test din adnotarile "# BACKTEST: ..." scrise DIRECT in
instruments.conf (research/backtest_ranges.py, text simplu — NU YAML/JSON,
decizie explicita user), ruleaza backtest REAL (research/monitortrades_backtest/
run_replay_backtest.py, peste cache_price_{symbol}.jsonl) pt fiecare valoare
din grid, si RECONFIGUREAZA + REPORNESTE monitortrades.py daca gaseste o
valoare clar mai buna — cu guardrail-uri explicite (user a cerut automatizare
completa, dar dupa ce am aratat azi ca acelasi max_budget=5000 a dat +$3016 in
o configurare si -$5279 in alta, pe ACELASI istoric):

  1. CONFIRMARE PE 2 FERESTRE INDEPENDENTE: istoricul disponibil e impartit in
     jumatate (prima/a doua) — o valoare e considerata "castigatoare" DOAR
     daca are cel mai bun avantaj fata de buy&hold (net - buy_hold) in AMBELE
     jumatati, nu doar una. Un rezultat care castiga doar pe o fereastra e
     tratat ca zgomot, nu semnal.
  2. DOAR valori din grid (niciodata extrapolare).
  3. MEDIE, nu salt direct (decizie user): valoarea aplicata efectiv =
     (valoare_configurata_azi + valoare_castigatoare_backtest) / 2 —
     amortizeaza exact genul de instabilitate demonstrat azi.
  4. RATE-LIMIT: un parametru nu se schimba mai des de o data la
     PILOT_MIN_DAYS_BETWEEN_CHANGES zile (implicit 7) — jurnal persistent.
  5. AUDIT: fiecare rulare scrie un rand in jurnal (testat, ambele ferestre,
     decizie, motiv) — independent daca s-a schimbat ceva sau nu.
  6. NOTIFICARE (alertnotifiers.notify, canalul deja folosit de flota): DOAR
     cand se aplica o schimbare reala (nu la fiecare rulare "nimic nou").
  7. KILL-SWITCH: env PILOT_DISABLED=true opreste TOTUL, fara sa atinga codul.

Rulare manuala (recomandat inainte de a fi pusa pe cron):
    python3 research/monitortrades_backtest/scheduled_pilot.py --dry-run
    python3 research/monitortrades_backtest/scheduled_pilot.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

ROOT = "/home/predut/binance"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
sys.path.insert(0, os.path.join(ROOT, "research", "monitortrades_backtest"))
os.chdir(ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from backtest_ranges import scan_backtest_ranges
import run_replay_backtest as rb
from providers.replay_provider import load_price_series

INSTRUMENTS_CONF = os.path.join(ROOT, "instruments.conf")
AUDIT_LOG = os.path.join(ROOT, "logger", "backtest_pilot_audit.jsonl")
MIN_DAYS_BETWEEN_CHANGES = float(os.environ.get("PILOT_MIN_DAYS_BETWEEN_CHANGES", "7"))
# Castigatorul trebuie sa bata valoarea CURENTA cu cel putin atata (net-buy_hold,
# USD) pe AMBELE ferestre. Altfel schimbarea e sub zgomot / parametrul e inert pe
# acest istoric (ex. gasit 28 iul: hard-TP nu se declanseaza -> toate valorile dau
# rezultate identice, iar max() pe egalitate "aplica" fals primul element din grila).
MIN_EDGE_MARGIN_USD = float(os.environ.get("PILOT_MIN_EDGE_MARGIN_USD", "1.0"))

# Chei in scope-ul pilotului (BTC/TAO, monitortrades) — restul adnotarilor din
# instruments.conf NU sunt atinse fara sa extindem explicit lista asta (decizie
# user: pilot restrans la monitortrades, nu toate modulele). Toate traiesc ca
# mt.* in instruments.conf, deci run_replay_backtest le fireaza deja prin params
# (verificat: is_trend_up neutralizat, maxage->since_s, hardtp->inst.param).
#   #4-5  gain/lost   (FACUT 23 iul: TAO mt.lost 4.9->5.25 aplicat)
#   #16   maxage_days (28 iul)
#   #15   hardtp / hardtp_fraction (28 iul; per-instrument, monitortrades.py:447)
PILOT_KEYS = {
    "BINANCE_BTC.mt.gain": ("BTCUSDC", "BTC", "mt.gain"),
    "BINANCE_BTC.mt.lost": ("BTCUSDC", "BTC", "mt.lost"),
    "BINANCE_TAO.mt.gain": ("TAOUSDC", "TAO", "mt.gain"),
    "BINANCE_TAO.mt.lost": ("TAOUSDC", "TAO", "mt.lost"),
    "BINANCE_BTC.mt.maxage_days": ("BTCUSDC", "BTC", "mt.maxage_days"),
    "BINANCE_TAO.mt.maxage_days": ("TAOUSDC", "TAO", "mt.maxage_days"),
    "BINANCE_BTC.mt.hardtp": ("BTCUSDC", "BTC", "mt.hardtp"),
    "BINANCE_TAO.mt.hardtp": ("TAOUSDC", "TAO", "mt.hardtp"),
    "BINANCE_BTC.mt.hardtp_fraction": ("BTCUSDC", "BTC", "mt.hardtp_fraction"),
    "BINANCE_TAO.mt.hardtp_fraction": ("TAOUSDC", "TAO", "mt.hardtp_fraction"),
}


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _git_head_short():
    """Commit-ul (scurt) al codului care a produs propunerea — pt ca prod sa stie
    exact ce versiune de motor/config a generat-o. '?' daca git nu raspunde."""
    import subprocess
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


def _append_audit(entry):
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    entry = dict(entry, ts=_now_iso())
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _last_change_for(full_key):
    """Cea mai recenta intrare din jurnal cu action='applied' pt full_key."""
    if not os.path.exists(AUDIT_LOG):
        return None
    last = None
    with open(AUDIT_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("full_key") == full_key and entry.get("action") == "applied":
                last = entry
    return last


def _current_value(section, key):
    """Citeste valoarea LIVE azi (nu grid-ul) direct din instruments.conf."""
    import configparser
    cp = configparser.ConfigParser()
    cp.read(INSTRUMENTS_CONF)
    return float(cp[section][key])


def _edge(pnl):
    """net - buy_hold — masura de "cat mai bine decat simpla detinere",
    comparabila intre ferestre diferite (spre deosebire de net brut)."""
    return pnl["net"] - pnl["buy_hold"]


def _num_str(x):
    """Reprezentare compacta a unei valori numerice pt grila (17.0->'17',
    0.5->'0.5', 5.25->'5.25') — folosita cand adaugam valoarea LIVE la testare."""
    return "%g" % x


def _split_series(symbol):
    """Istoricul disponibil, impartit in 2 jumatati (prima/a doua) — cele 2
    ferestre INDEPENDENTE cerute de guardrail-ul de confirmare."""
    path = os.path.join(ROOT, "cachedb", f"cache_price_{symbol}.jsonl")
    series = load_price_series(path, symbol)
    mid = len(series) // 2
    return series[:mid], series[mid:]


def _run_one(symbol, base, params, series):
    """Ruleaza logica REALA de backtest (identica structural cu
    run_replay_backtest.run_symbol()) peste o SERIE data direct (nu fisierul
    intreg de pe disc) — necesar ca sa putem testa cele 2 jumatati separat
    fara sa citim/impartim fisierul de fiecare data. NU scrie pnl.json (pilotul
    ruleaza zeci de variante -> ar genera zeci de foldere fara rost in
    logger/backtest/)."""
    if not series:
        return None
    provider = rb.ReplayMarketDataProvider({symbol: series}, fee_pct=rb.FEE_PCT)
    api = rb.MarketApi([provider])
    inst = rb.Instrument(name=symbol, symbol=symbol, provider="replay",
                         base=base, quote="USDC", params=dict(params), api=api)
    maxage_s = int(float(params["mt.maxage_days"]) * 24 * 3600)

    # 23 iul: is_trend_up() reala citeste cache-ul de trend LIVE (contamineaza
    # un replay istoric cu starea REALA curenta a pietei — vezi
    # run_replay_backtest._neutral_is_trend_up). Neutralizat identic aici.
    orig_is_trend_up = rb.mt.is_trend_up
    rb.mt.is_trend_up = rb._neutral_is_trend_up
    try:
        first_price = provider.advance(symbol)
        if first_price is None:
            return None
        last_price = first_price
        provider.place_order(symbol, "BUY", first_price, rb.SEED_NOTIONAL_USD / first_price)

        while True:
            price = provider.advance(symbol)
            if price is None:
                break
            last_price = price
            buys = provider.get_orders(symbol, "BUY", since_s=maxage_s)
            sells = provider.get_orders(symbol, "SELL", since_s=maxage_s)
            if not buys and not sells:
                provider.place_order(symbol, "BUY", price, rb.SEED_NOTIONAL_USD / price)
                continue
            try:
                rb.mt.monitor_price_and_trade(inst, sbs=rb.SBS, now_fn=lambda: provider.now(symbol))
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[{symbol}] eroare in monitor_price_and_trade: {e}\n")
    finally:
        rb.mt.is_trend_up = orig_is_trend_up

    all_buys = provider.get_orders(symbol, "BUY", since_s=1e12)
    all_sells = provider.get_orders(symbol, "SELL", since_s=1e12)
    total_bought = sum(o["qty"] * o["price"] for o in all_buys)
    total_sold = sum(o["qty"] * o["price"] for o in all_sells)
    open_qty, _ = provider.position(symbol)
    open_value = open_qty * last_price
    fees = sum(o["qty"] * o["price"] * rb.FEE_PCT / 100 for o in all_buys + all_sells)
    net = total_sold - total_bought + open_value - fees
    bh_qty = rb.SEED_NOTIONAL_USD / first_price
    buy_hold = (last_price - first_price) * bh_qty - 2 * bh_qty * first_price * rb.FEE_PCT / 100
    return {"net": round(net, 2), "buy_hold": round(buy_hold, 2)}


def evaluate_key(full_key, symbol, base, key, dry_run=True, propose=False):
    grid_values = scan_backtest_ranges(INSTRUMENTS_CONF).get(full_key)
    if not grid_values:
        return {"full_key": full_key, "action": "skipped", "reason": "no_grid_annotation"}

    section = full_key.split(".", 1)[0]
    current = _current_value(section, key)
    base_params = dict(rb.SYMBOLS[symbol]["params"])

    half1, half2 = _split_series(symbol)
    if len(half1) < 100 or len(half2) < 100:
        return {"full_key": full_key, "action": "skipped", "reason": "istoric insuficient pt 2 ferestre"}

    # Asigura ca valoarea LIVE curenta e printre cele testate: fara ea nu putem
    # compara castigatorul CU ea, iar un parametru INERT (toate valorile dau
    # rezultate identice) ar fi "aplicat" fals catre primul element din grila
    # (max() pe egalitate intoarce primul). Gasit 28 iul la #15 hardtp.
    test_values = list(grid_values)
    if not any(abs(float(v) - current) < 1e-9 for v in test_values):
        test_values.append(_num_str(current))

    results = {}
    for v in test_values:
        t0 = time.time()
        params = dict(base_params)
        params[key] = v
        r1 = _run_one(symbol, base, params, half1)
        r2 = _run_one(symbol, base, params, half2)
        sys.stderr.write(f"  [{full_key}] {key}={v}: half1={r1} half2={r2} ({time.time()-t0:.1f}s)\n")
        if r1 is None or r2 is None:
            continue
        results[v] = {"edge_half1": _edge(r1), "edge_half2": _edge(r2), "pnl1": r1, "pnl2": r2}

    if not results:
        return {"full_key": full_key, "action": "skipped", "reason": "backtest fara rezultate"}

    winner_half1 = max(results, key=lambda v: results[v]["edge_half1"])
    winner_half2 = max(results, key=lambda v: results[v]["edge_half2"])

    entry = {
        "full_key": full_key, "symbol": symbol, "current_value": current,
        "grid": grid_values, "results": results,
        "winner_half1": winner_half1, "winner_half2": winner_half2,
    }

    if winner_half1 != winner_half2:
        entry["action"] = "no_change"
        entry["reason"] = f"neconfirmat: castigator diferit pe cele 2 ferestre ({winner_half1} vs {winner_half2})"
        return entry

    winner = winner_half1
    winner_val = float(winner)
    if abs(winner_val - current) < 1e-9:
        entry["action"] = "no_change"
        entry["reason"] = "valoarea castigatoare = valoarea deja configurata"
        return entry

    # Castigatorul difera de valoarea curenta -> trebuie sa o BATA cu o marja
    # semnificativa pe AMBELE ferestre. Marja ~0 = parametru inert pe acest istoric
    # (ex. hard-TP nedeclansat), iar "castigatorul" e doar artefactul tie-break-ului.
    cur_key = next(v for v in results if abs(float(v) - current) < 1e-9)
    margin_h1 = results[winner]["edge_half1"] - results[cur_key]["edge_half1"]
    margin_h2 = results[winner]["edge_half2"] - results[cur_key]["edge_half2"]
    entry["margin_vs_current"] = {"half1": round(margin_h1, 2), "half2": round(margin_h2, 2)}
    if margin_h1 < MIN_EDGE_MARGIN_USD or margin_h2 < MIN_EDGE_MARGIN_USD:
        entry["action"] = "no_change"
        entry["reason"] = (f"castig neglijabil vs valoarea curenta {current} "
                            f"(+{margin_h1:.2f}/+{margin_h2:.2f} < {MIN_EDGE_MARGIN_USD} USD) "
                            f"— parametru posibil inert pe acest istoric")
        return entry

    # Mod DEV (--propose): castigatorul a trecut de guardrail-uri (confirmat pe 2
    # ferestre + bate valoarea curenta cu marja). NU aplicam/rate-limitam aici —
    # propunem valoarea BRUTA castigatoare; PROD decide la aplicare (media cu
    # valoarea LUI live, rate-limit, audit). Vezi UNIFIED_BACKTEST_PLAN.md §9.
    if propose:
        entry["winner_value"] = winner_val
        entry["action"] = "proposed"
        return entry

    last_change = _last_change_for(full_key)
    if last_change:
        last_ts = datetime.fromisoformat(last_change["ts"])
        if datetime.now() - last_ts < timedelta(days=MIN_DAYS_BETWEEN_CHANGES):
            entry["action"] = "rate_limited"
            entry["reason"] = (f"schimbat ultima data la {last_change['ts']}, "
                                f"asteapta {MIN_DAYS_BETWEEN_CHANGES} zile intre schimbari")
            return entry

    new_value = round((current + winner_val) / 2, 4)
    entry["proposed_new_value"] = new_value
    entry["action"] = "would_apply" if dry_run else "applied"

    if not dry_run:
        _apply_config_change(section, key, current, new_value)
        _restart_monitortrades()
        _notify_change(full_key, symbol, current, new_value, winner_val, entry)

    return entry


def _apply_config_change(section, key, old_value, new_value):
    """Inlocuieste DOAR valoarea numerica de pe linia `key = ...` din
    sectiunea `section`, pastrand tot restul fisierului (comentarii,
    formatare, alte sectiuni) neatins."""
    with open(INSTRUMENTS_CONF, encoding="utf-8") as f:
        lines = f.readlines()
    in_section = False
    key_re = re.compile(rf'^(\s*{re.escape(key)}\s*=\s*)([^\s#]+)(.*)$')
    for i, line in enumerate(lines):
        sm = re.match(r'^\s*\[([^\]]+)\]\s*$', line)
        if sm:
            in_section = (sm.group(1) == section)
            continue
        if in_section:
            m = key_re.match(line)
            if m:
                lines[i] = f"{m.group(1)}{new_value}{m.group(3)}\n"
                break
    with open(INSTRUMENTS_CONF, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _restart_monitortrades():
    """Omoara procesul live — supervisor-ul flota_start.sh il reporneste
    automat (acelasi mecanism folosit manual toata sesiunea)."""
    import subprocess
    try:
        out = subprocess.run(["pgrep", "-f", "python monitortrades.py"],
                              capture_output=True, text=True, timeout=5)
        pids = [p for p in out.stdout.split() if p.isdigit()]
        for pid in pids:
            subprocess.run(["kill", pid], timeout=5)
    except Exception as e:  # noqa: BLE001 — nu opreste jurnalizarea/notificarea
        print(f"[scheduled_pilot] eroare la restart monitortrades: {e}")


def _notify_change(full_key, symbol, old_value, new_value, winner_val, entry):
    try:
        import alertnotifiers as alert
        body = (f"{full_key}: {old_value} -> {new_value} "
                f"(castigator backtest confirmat pe 2 ferestre: {winner_val}, "
                f"medie cu valoarea veche)")
        alert.notify(title="Pilot backtest: config schimbat", body=body,
                     source="scheduled_pilot.py", symbol=symbol)
    except Exception as e:  # noqa: BLE001
        print(f"[scheduled_pilot] eroare notificare: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                     help="evalueaza si raporteaza, dar NU schimba config/restart botul")
    ap.add_argument("--only", default="",
                     help="ruleaza DOAR cheile care contin unul din aceste substring-uri "
                          "separate prin virgula (ex. 'maxage,hardtp' sau 'BINANCE_TAO'); gol = toate")
    ap.add_argument("--propose", action="store_true",
                     help="mod DEV: NU aplica/reporni; scrie propunerile confirmate (valoare "
                          "castigatoare bruta) in --propose-out, pt fluxul git dev->prod")
    ap.add_argument("--propose-out", default=os.path.join(ROOT, "backtest_proposals.json"),
                     help="unde scrie propunerile in modul --propose (implicit backtest_proposals.json)")
    args = ap.parse_args()

    if os.environ.get("PILOT_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        print("[scheduled_pilot] PILOT_DISABLED=true -- ies fara sa fac nimic")
        return

    # monitor_price_and_trade() e f-oarte "vorbaret" (print() la fiecare tick) —
    # pilotul ruleaza zeci de variante peste sute de mii de tick-uri, deci
    # suprimarea e necesara (altfel I/O-ul de consola domina timpul de rulare).
    rb.mt.log.disable_print()

    only_terms = [t.strip() for t in args.only.split(",") if t.strip()]
    keys = {k: v for k, v in PILOT_KEYS.items()
            if not only_terms or any(t in k for t in only_terms)}
    if not keys:
        print(f"[scheduled_pilot] nicio cheie nu contine '{args.only}' -- ies")
        return

    proposals = []
    for full_key, (symbol, base, key) in keys.items():
        print(f"=== {full_key} ===")
        entry = evaluate_key(full_key, symbol, base, key,
                              dry_run=args.dry_run, propose=args.propose)
        _append_audit(entry)
        print(json.dumps({k: v for k, v in entry.items() if k != "results"}, indent=2, default=str))
        if args.propose and entry.get("action") == "proposed":
            proposals.append({
                "ts": entry["ts"], "full_key": full_key,
                "section": full_key.split(".", 1)[0], "key": key, "symbol": symbol,
                "current_on_dev": entry["current_value"],
                "winner_value": entry["winner_value"],
                "margin_vs_current": entry.get("margin_vs_current"),
                "dev_commit": _git_head_short(),
            })

    if args.propose:
        # snapshot al propunerilor curente (suprascrie — fisierul = "ce propune dev
        # ACUM"; prod le consuma si aplica cu propriile guardrail-uri). Gol = niciun
        # semnal confirmat in acest ciclu (prod nu are ce aplica).
        with open(args.propose_out, "w", encoding="utf-8") as f:
            json.dump(proposals, f, indent=2, default=str)
        print(f"\n[scheduled_pilot] {len(proposals)} propunere(i) scrise in {args.propose_out}")


if __name__ == "__main__":
    main()
