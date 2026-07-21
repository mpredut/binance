#!/usr/bin/env python3
"""
cache_watchdog.py — verifică prospețimea TUTUROR cache-urilor (cachedb/cache_*.json)
și alarmează dacă vreunul s-a învechit (cacheManager/priceAnalysis murite silențios).

Rulează ca task scurt din cron (la fiecare 5 min), independent de flotă.

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
# Praguri per-cache (min): cele lente (trend lung, valoare activ) se actualizeaza rar.
# Cache-urile de order/trade sunt EVENT-DRIVEN: cacheManager le rescrie DOAR cand apare
# un order/trade nou pe exchange. Intr-o perioada linistita (fara fill-uri) mtime-ul lor
# imbatraneste natural peste 20 min -> fals pozitiv. Nu ascund o cadere reala a flotei:
# daca flota moare, cache-urile RAPIDE de pret (cache_currentprice prag 20, cache_asset_value
# prag 60) declanseaza alarma oricum. Le dau prag mare, doar ca plasa de siguranta pt un
# cache cu adevarat blocat (>24h fara nimic e suspect chiar si intr-o piata moarta).
_STALE_OVERRIDES = {
    "cache_price_long_trend.json": 90,
    "cache_asset_value.json": 60,
    "cache_T_trend.json": 11520,   # T empiric per moneda: recalc la 7 zile -> prag 8 zile
    # Event-driven (continut nou DOAR la order/trade nou): sub semantica pe
    # CONTINUT (19 iul), perioadele linistite >24h sunt legitime (masurat: 33h
    # fara fill-uri cu toate BUY-urile refuzate de weight-limit) -> prag 72h.
    "cache_order.json": 4320,
    "cache_trade.json": 4320,
    "cache_trade_kraken.json": 4320,
}


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


def check_once(now=None):
    """Verifică TOATE cache_*.json din cachedb/. Alertă dacă vreunul e stale (peste
    pragul lui) și nu suntem în cooldown. Întoarce True dacă a trimis alertă."""
    now = now if now is not None else time.time()
    files = _cache_files()
    stale = []
    if not files:
        stale.append(("(niciun cache_*.json)", float("inf"), STALE_MINUTES,
                      f"{_CACHE_DIR} gol sau lipsește"))
    for p in files:
        freshness, detail = cache_freshness_seconds(p)
        age_min = (now - freshness) / 60.0 if freshness > 0 else float("inf")
        thr = _STALE_OVERRIDES.get(p.name, STALE_MINUTES)
        if age_min > thr:
            stale.append((p.name, age_min, thr, detail))

    if not stale:
        print(f"[watchdog] OK — {len(files)} cache-uri proaspete")
        return False

    # cooldown: nu re-alarma prea des
    state = wc.load_state(STATE_FILE)
    last = state.get("last_alert_ts", 0)
    if (now - last) < COOLDOWN_MINUTES * 60:
        print(f"[watchdog] STALE ({', '.join(s[0] for s in stale)}) dar în cooldown — nu re-alarmez")
        return False

    lines = []
    for name, age_min, thr, detail in stale:
        age_txt = f"{age_min:.0f} min" if age_min != float("inf") else "∞"
        lines.append(f"  • {name}: {age_txt} (prag {thr:.0f} min) — {detail}")
    title = "⚠️ Cache STALE pe server"
    message = ("Cache-uri învechite (probabil cacheManager/priceAnalysis s-au oprit):\n"
               + "\n".join(lines)
               + "\nVerifică flota (flota_start) și repornește.")
    print(f"[watchdog] ALARMĂ:\n{message}")
    wc.send_ntfy(title, message)
    wc.send_email(title, message)
    state["last_alert_ts"] = now
    wc.save_state(STATE_FILE, state)
    return True


if __name__ == "__main__":
    sent = check_once()
    sys.exit(2 if sent else 0)
