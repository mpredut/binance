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
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent   # verify_tools/ -> rădăcina repo

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")                   # secrete comune (gitignored)
    load_dotenv(_ROOT / "config.env")             # config versionat (comis)
except Exception:
    pass

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
    "cache_order.json": 1440,      # event-driven: se scrie doar la order nou (prag 24h)
    "cache_trade.json": 1440,      # event-driven: se scrie doar la trade nou (prag 24h)
    "cache_trade_kraken.json": 1440,  # idem, fill-urile Kraken
}


def _cache_files():
    """Toate cache_*.json din cachedb/ (exclude .bak/.tmp)."""
    return sorted(p for p in _CACHE_DIR.glob("cache_*.json")
                  if not p.name.endswith((".bak", ".tmp")))


def _normalize_ts_seconds(value):
    """fetchtime poate fi în ms (>1e12) sau secunde → întoarce secunde (float)."""
    if not isinstance(value, (int, float)) or value <= 0:
        return 0.0
    return value / 1000.0 if value > 1e12 else float(value)


def cache_freshness_seconds(path):
    """Cel mai recent semnal de prospețime (epoch secunde): max(fetchtime, mtime fișier).
    Întoarce (freshness_sec, detalii) sau (0, motiv) dacă fișierul lipsește/e corupt."""
    p = Path(path)
    if not p.exists():
        return 0.0, f"fișierul {p.name} nu există"
    newest = 0.0
    try:
        data = json.load(open(p))
        ft = data.get("fetchtime", {}) if isinstance(data, dict) else {}
        for v in ft.values():
            newest = max(newest, _normalize_ts_seconds(v))
    except Exception as e:
        return 0.0, f"cache corupt: {e}"
    try:
        newest = max(newest, p.stat().st_mtime)
    except OSError:
        pass
    return newest, "ok"


def _load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def _save_state(state):
    try:
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[watchdog] nu pot scrie state: {e}")


def _send_ntfy(title, message):
    url = os.environ.get("PHONE_ALERT_URL")
    if not url and os.environ.get("NTFY_TOPIC"):
        url = f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}"
    if not url:
        print("[watchdog] fără PHONE_ALERT_URL/NTFY_TOPIC — sar push-ul")
        return False
    try:
        import requests
        # ntfy decodează Title ca UTF-8 → trecem octeții UTF-8 prin latin-1, ca să
        # păstrăm caractere non-ASCII (emoji, simboluri non-latine) în titlu.
        utf8_title = title.encode("utf-8").decode("latin-1")
        if "ntfy.sh/" in url:
            r = requests.post(url, data=message.encode("utf-8"),
                              headers={"Title": utf8_title, "Priority": "urgent",
                                       "Tags": "warning"}, timeout=10)
        else:
            r = requests.post(url, json={"title": title, "message": message}, timeout=10)
        ok = r.status_code < 400
        print(f"[watchdog] push {'OK' if ok else 'EȘUAT ' + str(r.status_code)}")
        return ok
    except Exception as e:
        print(f"[watchdog] push excepție: {e}")
        return False


def _send_email(subject, body):
    user = os.environ.get("SMTP_USERNAME")
    pwd = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("ALERT_TO_EMAIL")
    if not (user and pwd and to):
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"], msg["To"], msg["Subject"] = user, to, subject
        with smtplib.SMTP(os.environ.get("SMTP_SERVER", "smtp.gmail.com"),
                          int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(user, [to], msg.as_string())
        print("[watchdog] email trimis")
        return True
    except Exception as e:
        print(f"[watchdog] email excepție: {e}")
        return False


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
    state = _load_state()
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
    _send_ntfy(title, message)
    _send_email(title, message)
    state["last_alert_ts"] = now
    _save_state(state)
    return True


if __name__ == "__main__":
    sent = check_once()
    sys.exit(2 if sent else 0)
