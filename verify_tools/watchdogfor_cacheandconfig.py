#!/usr/bin/env python3
"""
watchdogfor_cacheandconfig.py — DOUA responsabilitati intr-un singur watchdog:

1. CACHE: verifică prospețimea TUTUROR cache-urilor (cachedb/cache_*.json) și
   alarmează / (optional) repornește cacheManager dacă vreunul s-a învechit
   (cacheManager/priceAnalysis murite silențios).
2. CONFIG (check_configs_once): detectează schimbări de CONȚINUT în fișierele de
   config (instruments.conf etc.) și repornește procesul proprietar — inclusiv la
   editări manuale. Respawn-safe prin healthcheck.sh --supervise (procs.conf).
   Kill-switch WATCHDOG_CONFIG_RESTART (implicit off, activat din config.env).

Rulează ca task scurt din cron (la fiecare 2 min), independent de flotă.
(fost watchdogfor_cache.py — redenumit 28 iul cand a primit si config-watch.)

Semnal de prospețime per fișier: max(fetchtime din cache, mtime fișier). Dacă vârsta
depășește pragul (per-cache sau WATCHDOG_STALE_MINUTES) → alertă (ntfy + email), cu cooldown.
(fost price_monitor_watchdog.py, care verifica un singur cache)

Variabile de mediu (din .env / config.env din rădăcină):
  PHONE_ALERT_URL / NTFY_TOPIC   — canal push
  SMTP_USERNAME / SMTP_PASSWORD / ALERT_TO_EMAIL — email (opțional)
  WATCHDOG_STALE_MINUTES      (default 20; cache-urile lente au prag mai mare)
  WATCHDOG_COOLDOWN_MINUTES   (default 60)
  BINANCE_CACHE_DIR           (default <radacina>/cachedb)
"""
import os
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog_common as wc       # infrastructura partajata: env, ntfy/email, state

_ROOT = wc.ROOT                                   # verify_tools/ -> rădăcina repo
wc.load_env()

# Cache-urile stau in subfolderul cachedb/ (BINANCE_CACHE_DIR il poate suprascrie).
_CACHE_DIR = Path(os.environ.get("BINANCE_CACHE_DIR", _ROOT / "cachedb"))
STATE_FILE = _ROOT / ".watchdog_state.json"
STALE_MINUTES = float(os.environ.get("WATCHDOG_STALE_MINUTES", "20"))
COOLDOWN_MINUTES = float(os.environ.get("WATCHDOG_COOLDOWN_MINUTES", "60"))

# 28 iul: AUTO-RESTART (cerere user, dupa incidentul HYPE care a cerut restart manual).
# Cand un cache de pret RAPID (prag default, scris de cacheManager) e stale = flota
# chiar are o problema reala -> watchdog-ul reporneste cacheManager singur (supervisor-ul
# flota_start il respawneaza in ~30s), pe langa alerta. Guardrail-uri contra buclelor:
# cooldown intre restarturi + plafon per fereastra (peste care se OPRESTE si cere
# interventie manuala). Kill-switch: WATCHDOG_AUTO_RESTART=false.
AUTO_RESTART = os.environ.get("WATCHDOG_AUTO_RESTART", "false").strip().lower() in ("1", "true", "yes", "on", "da")
AUTO_RESTART_COOLDOWN_MIN = float(os.environ.get("WATCHDOG_AUTO_RESTART_COOLDOWN_MIN", "15"))
AUTO_RESTART_MAX = int(os.environ.get("WATCHDOG_AUTO_RESTART_MAX", "3"))
AUTO_RESTART_WINDOW_H = float(os.environ.get("WATCHDOG_AUTO_RESTART_WINDOW_H", "6"))
AUTO_RESTART_TARGET = "python cacheManager.py"   # pattern pgrep/pkill; flota_start respawneaza

# ── CONFIG-WATCH: reporneste procesul proprietar cand un fisier de config s-a
# schimbat. Detectie pe HASH de CONTINUT (nu mtime — o atingere fara schimbare
# reala nu declanseaza reporniri false). Prinde SI editari manuale (azi trebuie
# sa-ti amintesti sa repornesti dupa ce editezi un config). Toate procesele-tinta
# sunt in procs.conf => respawn-safe prin healthcheck.sh --supervise (cron */5).
# Kill-switch: WATCHDOG_CONFIG_RESTART (implicit false; activat din config.env).
CONFIG_RESTART = os.environ.get("WATCHDOG_CONFIG_RESTART", "false").strip().lower() in ("1", "true", "yes", "on", "da")
CONFIG_RESTART_COOLDOWN_MIN = float(os.environ.get("WATCHDOG_CONFIG_COOLDOWN_MIN", "5"))
CONFIG_RESTART_MAX = int(os.environ.get("WATCHDOG_CONFIG_MAX", "5"))
CONFIG_RESTART_WINDOW_H = float(os.environ.get("WATCHDOG_CONFIG_WINDOW_H", "6"))
# config (relativ la radacina) -> procese proprietare (pattern pkill -f). Un config
# cu mai multi consumatori (instruments.conf: monitortrades SI tradeall) -> repornim
# pe toti. Config-uri partajate/secrete (config.env, .env) NU sunt aici deliberat
# (prea larg pt auto-restart; o schimbare acolo ar cere restart de flota, decizie umana).
_CONFIG_OWNERS = {
    "instruments.conf": ["monitortrades.py", "tradeall.py"],
    "monitortrades.conf": ["monitortrades.py"],
    "monitortrades_config.env": ["monitortrades.py"],
    "tradeall_config.env": ["tradeall.py"],
    "rtrade_config.env": ["rtrade.py"],
    "assetguardian_config.env": ["assetguardian.py"],
}

# Praguri per-cache (min): cele lente (trend lung, valoare activ) se actualizeaza rar.
# Cache-urile de order/trade sunt EVENT-DRIVEN: cacheManager le rescrie DOAR cand apare
# un order/trade nou pe exchange. Intr-o perioada linistita (fara fill-uri) mtime-ul lor
# imbatraneste natural peste 20 min -> fals pozitiv. Nu ascund o cadere reala a flotei:
# daca flota moare, cache-urile RAPIDE de pret (cache_currentprice prag 20, cache_asset_value
# prag 60) declanseaza alarma oricum. Le dau prag mare, doar ca plasa de siguranta pt un
# cache cu adevarat blocat (>24h fara nimic e suspect chiar si intr-o piata moarta).
_STALE_OVERRIDES = {
    # 28 iul: 90 -> 1440 (24h). Vechea valoare de 90 min CONTRAZICEA filosofia
    # documentata mai sus pt cache-urile lente ("prag mare, plasa de siguranta,
    # >24h suspect"). cache_price_long_trend.json e scris DOAR cand
    # detect_long_term_trend() gaseste un trend Mann-Kendall SEMNIFICATIV
    # (priceAnalysis.py:461 — altfel "indeterminabil", nu scrie nimic). Intr-o
    # piata choppy/laterala unde MK nu e semnificativ, continutul imbatraneste
    # legitim ore intregi -> 90 min declansa alarme false constante (alarm
    # fatigue = risc sa ascunda o alarma REALA). Daca priceAnalysis chiar moare,
    # cache_currentprice (prag 20) alarmeaza oricum in <20 min.
    "cache_price_long_trend.json": 1440,
    "cache_asset_value.json": 60,
    "cache_T_trend.json": 11520,   # T empiric per moneda: recalc la 7 zile -> prag 8 zile
    # Event-driven (continut nou DOAR la order/trade nou): sub semantica pe
    # CONTINUT (19 iul), perioadele linistite >24h sunt legitime (masurat: 33h
    # fara fill-uri cu toate BUY-urile refuzate de weight-limit) -> prag 72h.
    "cache_order.json": 4320,
    "cache_trade.json": 4320,
    "cache_trade_kraken.json": 4320,
}

# 28 iul: gating pe "flota vie" pt cache-urile EVENT-DRIVEN (fill-uri order/trade).
# Motiv: frecventa de update a acestora e determinata de PIATA (cand apare un fill),
# NU de sanatatea flotei — masurat 28 iul: BTC 8 zile FARA fill (pozitie vanduta pe
# 19 iul, piata in scadere, kalman fara intrari valide), TOATE cache-urile de pret
# proaspete (0-5 min). Pragul de 72h declansa deci alarme false. Filosofia deja
# documentata mai sus o spune: "daca flota moare, cache-urile RAPIDE de pret
# declanseaza alarma oricum" — deci fill-cache-urile NU trebuie sa detecteze
# independent moartea flotei. Regula: daca un cache "fleet-alive" (pret rapid) e
# PROASPAT, flota e demonstrabil vie -> staleness pe order/trade e benigna
# (doar "n-au fost fill-uri"), NU alarma. Fail-safe: daca NU putem confirma flota
# vie (toate cache-urile de pret stale = flota chiar moarta), alarma TRECE normal.
# PLAFON DUR: peste 30 zile alarmeaza oricum, chiar cu flota vie — atunci
# fill-tracking-ul insusi (WS event sync) e probabil rupt, nu doar piata linistita.
_EVENT_DRIVEN_CACHES = {"cache_order.json", "cache_trade.json", "cache_trade_kraken.json"}
_FLEET_ALIVE_CACHES = {"cache_prices_multi.json", "cache_currentprice.json", "cache_instant_trend.json"}
_EVENT_DRIVEN_HARD_CEILING_MIN = 43200   # 30 zile: peste asta fill-tracking suspect chiar si cu flota vie

# 28 iul: prag DEDICAT, mai STRANS, pt cache-urile de pret cu adevarat RAPIDE
# (~1s cadenta: WS Binance / poller non-Binance). Pragul general de 20 min era
# dimensionat pt cel mai LENT cache "rapid" (arhiva sparse cache_price_*.jsonl,
# ~7 min) -> mult prea larg pt cele de 1s. Un stall de 5 min pe un cache de 1s
# = ~300 update-uri ratate = problema reala (nu un blip). Astea sunt SI singurele
# care declanseaza auto-restart-ul: cacheManager e cel care le scrie, deci
# staleness-ul lor = cacheManager rupt = restart-ul chiar ajuta. Arhiva sparse
# (.jsonl) ramane pe pragul general (are nevoie de marja).
_FAST_PRICE_THRESHOLD_MIN = float(os.environ.get("WATCHDOG_FAST_PRICE_MINUTES", "5"))


def _is_fast_price_cache(name):
    """True pt cache-urile de pret RAPIDE (~1s): cele care primesc prag strans
    SI declanseaza auto-restart. Exclude .jsonl (arhiva sparse ~7min / arhivator
    ~60s) care raman pe pragul general."""
    if name in ("cache_currentprice.json", "cache_prices_multi.json", "cache_instant_trend.json"):
        return True
    if name.startswith("cache_24price_") and name.endswith(".json"):   # per-simbol, WS/poll ~1-20s
        return True
    return False


def _threshold_for(name):
    """Pragul de staleness (min) pt un cache: fast-price -> prag strans; altfel
    override-ul lui slow/event-driven; altfel default."""
    if _is_fast_price_cache(name):
        return _FAST_PRICE_THRESHOLD_MIN
    return _STALE_OVERRIDES.get(name, STALE_MINUTES)


def _cache_files():
    """Toate cache_*.json SI cache_*.jsonl din cachedb/ (exclude .bak/.tmp/.meta).
    21 iul: cache_24price_long_*.jsonl (arhivatorul) devenise invizibil aici
    dupa migrarea la JSONL — glob-ul verifica DOAR .json, deci watchdog-ul nu
    mai alerta nici macar cand arhivatorul sta oprit zile intregi."""
    patterns = ("cache_*.json", "cache_*.jsonl")
    files = {p for pat in patterns for p in _CACHE_DIR.glob(pat)}
    return sorted(p for p in files if not p.name.endswith((".bak", ".tmp", ".meta")))


def _normalize_ts_seconds(value):
    """fetchtime poate fi în ms (>1e12) sau secunde → întoarce secunde (float)."""
    if not isinstance(value, (int, float)) or value <= 0:
        return 0.0
    return value / 1000.0 if value > 1e12 else float(value)


def cache_freshness_seconds(path):
    """Cel mai recent semnal de prospețime (epoch secunde), din CONTINUT:
    fetchtime sau campurile "ts" per simbol. mtime e DOAR fallback cand
    continutul nu are niciun timestamp — NU se combina cu max(): cacheManager
    salveaza periodic si date INGHETATE (incident 19 iul: DNS cazut, preturi
    vechi de 27 min, dar mtime proaspat la fiecare save -> watchdog orb).
    Întoarce (freshness_sec, detalii) sau (0, motiv) dacă lipsește/e corupt."""
    p = Path(path)
    if not p.exists():
        return 0.0, f"fișierul {p.name} nu există"
    newest = 0.0
    if p.name.endswith(".jsonl"):
        # 21 iul: json.load() pe un fisier JSONL (linie-cu-linie, nu UN obiect)
        # arunca eroare -> raporta gresit "cache corupt" (freshness=0, alarma
        # falsa la fiecare rulare). Citim doar COADA (fisierul poate fi zeci
        # de MB) si luam ts-ul din ULTIMA linie completa.
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8192))
                chunk = f.read().decode("utf-8", errors="replace")
            lines = [l for l in chunk.split("\n") if l.strip()]
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                    ts = rec.get("i", [0])[0] if isinstance(rec.get("i"), list) else 0
                    if ts:
                        newest = _normalize_ts_seconds(ts)
                        break
                except (json.JSONDecodeError, TypeError, IndexError):
                    continue   # posibil linia taiata de seek — incercam pe cea de dinainte
        except OSError as e:
            return 0.0, f"cache corupt: {e}"
    else:
        try:
            data = json.load(open(p))
            if isinstance(data, dict):
                for v in data.get("fetchtime", {}).values():
                    newest = max(newest, _normalize_ts_seconds(v))
                if newest == 0.0:
                    # fara fetchtime (ex. cache_instant_trend): cauta "ts" per simbol
                    for v in data.values():
                        if isinstance(v, dict):
                            newest = max(newest, _normalize_ts_seconds(v.get("ts", 0)))
        except Exception as e:
            return 0.0, f"cache corupt: {e}"
    if newest > 0.0:
        return newest, "continut"
    try:
        return p.stat().st_mtime, "mtime (continut fara timestamp)"
    except OSError:
        return 0.0, "mtime indisponibil"


def _do_restart(target=AUTO_RESTART_TARGET):
    """Omoara procesul-tinta (cacheManager); supervisor-ul flota_start il respawneaza.
    Izolat pt testare (se poate mock-ui). Intoarce True daca pkill a rulat fara eroare."""
    import subprocess
    subprocess.run(["pkill", "-f", target], timeout=10, check=False)
    return True


def _config_hash(path):
    """SHA-256 al CONTINUTULUI (nu mtime). None daca fisierul lipseste."""
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


def check_configs_once(now=None):
    """Detecteaza schimbari de CONTINUT in fisierele de config (_CONFIG_OWNERS) si
    reporneste procesele proprietare (respawn-safe prin procs.conf). Prima vedere a
    unui fisier = doar baseline (nu reporneste). Debounce: hash-ul se actualizeaza pe
    loc, deci o schimbare declanseaza O SINGURA data. Guardrail-uri: cooldown + plafon
    (ca la auto-restart de cache) + kill-switch CONFIG_RESTART. Intoarce lista de
    procese repornite."""
    now = now if now is not None else time.time()
    state = wc.load_state(STATE_FILE)
    hashes = state.setdefault("config_hashes", {})

    changed = []
    for name in _CONFIG_OWNERS:
        h = _config_hash(str(_ROOT / name))
        if h is None:
            continue
        prev = hashes.get(name)
        hashes[name] = h                      # actualizeaza mereu (baseline / debounce)
        if prev is not None and h != prev:
            changed.append(name)

    if not changed:
        wc.save_state(STATE_FILE, state)
        return []

    owners = sorted({o for n in changed for o in _CONFIG_OWNERS[n]})
    note = f"config schimbat: {', '.join(changed)} -> proprietari: {', '.join(owners)}"

    if not CONFIG_RESTART:
        print(f"[watchdog] {note} — WATCHDOG_CONFIG_RESTART=false, doar notific")
        wc.send_ntfy("⚙️ Config schimbat", note + "\n(auto-restart OFF; reporneste manual daca e nevoie)")
        wc.save_state(STATE_FILE, state)
        return []

    hist = [t for t in state.get("config_restart_history", []) if now - t < CONFIG_RESTART_WINDOW_H * 3600]
    if hist and (now - max(hist)) < CONFIG_RESTART_COOLDOWN_MIN * 60:
        print(f"[watchdog] {note} — dar in COOLDOWN ({CONFIG_RESTART_COOLDOWN_MIN:.0f}min), nu repornesc acum")
        wc.save_state(STATE_FILE, state)
        return []
    if len(hist) >= CONFIG_RESTART_MAX:
        msg = f"⛔ PLAFON config-restart ({CONFIG_RESTART_MAX} in {CONFIG_RESTART_WINDOW_H:.0f}h). {note}. NU repornesc — verifica manual."
        print(f"[watchdog] {msg}")
        wc.send_ntfy("⛔ Config-restart plafonat", msg)
        wc.save_state(STATE_FILE, state)
        return []

    for pat in owners:
        _do_restart(pat)
    hist.append(now)
    state["config_restart_history"] = hist
    wc.save_state(STATE_FILE, state)

    msg = (f"🔄 {', '.join(changed)} s-a schimbat -> repornit {', '.join(owners)} "
           f"(respawn prin healthcheck --supervise). Restart {len(hist)}/{CONFIG_RESTART_MAX} in {CONFIG_RESTART_WINDOW_H:.0f}h.")
    print(f"[watchdog] {msg}")
    wc.send_ntfy("🔄 Config schimbat -> restart", msg)
    wc.send_email("Config schimbat -> restart proces", msg)
    return owners


def _maybe_auto_restart(stale, now, state):
    """Daca AUTO_RESTART e activat SI un cache de pret RAPID (nu unul din
    _STALE_OVERRIDES = slow/event-driven) e stale, reporneste cacheManager — cu
    cooldown + plafon per fereastra. Intoarce (restarted: bool, note: str).
    Modifica state['auto_restart_history'] cand reporneste."""
    if not AUTO_RESTART:
        return False, ""
    # Doar cache-urile de pret RAPIDE (scrise de cacheManager, ~1s) declanseaza.
    # Cele slow/event-driven (long-trend, asset_value, fill-uri) SI arhiva sparse
    # (.jsonl, alt proces / cadenta lenta) NU justifica un restart al cacheManager.
    critical = [name for (name, _age, _thr, _det) in stale if _is_fast_price_cache(name)]
    if not critical:
        return False, ""
    hist = [t for t in state.get("auto_restart_history", []) if now - t < AUTO_RESTART_WINDOW_H * 3600]
    if hist and (now - max(hist)) < AUTO_RESTART_COOLDOWN_MIN * 60:
        return False, (f"auto-restart in COOLDOWN ({AUTO_RESTART_COOLDOWN_MIN:.0f}min de la ultimul) "
                       f"— doar alertez, cacheManager NErepornit")
    if len(hist) >= AUTO_RESTART_MAX:
        return False, (f"⛔ PLAFON auto-restart atins ({AUTO_RESTART_MAX} in {AUTO_RESTART_WINDOW_H:.0f}h) "
                       f"— INTERVENTIE MANUALA necesara, nu mai repornesc automat")
    try:
        _do_restart()
        hist.append(now)
        state["auto_restart_history"] = hist
        return True, (f"🔁 cacheManager REPORNIT automat (cache stale: {', '.join(critical)}). "
                      f"Restart {len(hist)}/{AUTO_RESTART_MAX} in fereastra de {AUTO_RESTART_WINDOW_H:.0f}h.")
    except Exception as e:  # noqa: BLE001 — un restart esuat nu trebuie sa opreasca alerta
        return False, f"auto-restart ESUAT ({e}) — reporneste MANUAL flota"


def check_once(now=None):
    """Verifică TOATE cache_*.json din cachedb/. Alertă dacă vreunul e stale (peste
    pragul lui) și nu suntem în cooldown. Întoarce True dacă a trimis alertă."""
    now = now if now is not None else time.time()
    files = _cache_files()
    stale = []
    fleet_alive = False   # True daca un cache "fleet-alive" (pret rapid) e proaspat
    if not files:
        stale.append(("(niciun cache_*.json)", float("inf"), STALE_MINUTES,
                      f"{_CACHE_DIR} gol sau lipsește"))
    for p in files:
        freshness, detail = cache_freshness_seconds(p)
        age_min = (now - freshness) / 60.0 if freshness > 0 else float("inf")
        thr = _threshold_for(p.name)
        if p.name in _FLEET_ALIVE_CACHES and age_min <= thr:
            fleet_alive = True
        if age_min > thr:
            stale.append((p.name, age_min, thr, detail))

    # Gating flota-vie: daca flota e demonstrabil vie (un cache de pret rapid e
    # proaspat), staleness pe cache-urile EVENT-DRIVEN (fill-uri) e benigna
    # (doar "n-au fost fill-uri") si NU se alarmeaza — pana la plafonul dur, peste
    # care fill-tracking-ul insusi e suspect. Fail-safe: daca flota NU e confirmata
    # vie, nu se suprima nimic. Vezi nota de la _EVENT_DRIVEN_CACHES.
    if fleet_alive:
        suppressed = [s for s in stale
                      if s[0] in _EVENT_DRIVEN_CACHES and s[1] < _EVENT_DRIVEN_HARD_CEILING_MIN]
        if suppressed:
            names = ", ".join(s[0] for s in suppressed)
            print(f"[watchdog] {names} stale dar flota e vie (pret proaspat) — benign, nu alarmez")
        stale = [s for s in stale if s not in suppressed]

    if not stale:
        print(f"[watchdog] OK — {len(files)} cache-uri proaspete")
        return False

    state = wc.load_state(STATE_FILE)

    # AUTO-RESTART: independent de cooldown-ul de ALERTA (are guardrail-urile lui).
    # Modifica state['auto_restart_history'] daca reporneste efectiv.
    restarted, restart_note = _maybe_auto_restart(stale, now, state)
    if restart_note:
        print(f"[watchdog] {restart_note}")

    # Cooldown de alerta: nu re-alarma prea des. DAR un restart efectiv trece peste
    # cooldown (eveniment important — user-ul trebuie sa stie ca s-a repornit).
    last = state.get("last_alert_ts", 0)
    if (now - last) < COOLDOWN_MINUTES * 60 and not restarted:
        print(f"[watchdog] STALE ({', '.join(s[0] for s in stale)}) dar în cooldown — nu re-alarmez")
        wc.save_state(STATE_FILE, state)   # persista auto_restart_history chiar si fara alerta
        return False

    lines = []
    for name, age_min, thr, detail in stale:
        age_txt = f"{age_min:.0f} min" if age_min != float("inf") else "∞"
        lines.append(f"  • {name}: {age_txt} (prag {thr:.0f} min) — {detail}")
    title = "⚠️ Cache STALE pe server"
    message = ("Cache-uri învechite (probabil cacheManager/priceAnalysis s-au oprit):\n"
               + "\n".join(lines))
    message += ("\n\n" + restart_note) if restart_note else "\nVerifică flota (flota_start) și repornește."
    print(f"[watchdog] ALARMĂ:\n{message}")
    wc.send_ntfy(title, message)
    wc.send_email(title, message)
    state["last_alert_ts"] = now
    wc.save_state(STATE_FILE, state)
    return True


if __name__ == "__main__":
    # Config-watch intai (isi salveaza starea cu config_hashes/istoric), apoi cache.
    # Cele doua ating chei DISJUNCTE din STATE_FILE, iar check_once salveaza doar la
    # staleness -> config_hashes persista corect intre rulari.
    check_configs_once()
    sent = check_once()
    sys.exit(2 if sent else 0)
