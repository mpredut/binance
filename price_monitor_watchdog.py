#!/usr/bin/env python3
"""
price_monitor_watchdog.py — verifică prospețimea cache-ului de prețuri și alarmează
dacă monitorul de preț (run_price_monitor.py) s-a oprit sau nu mai actualizează.

Rulează ca task scurt (de ex. din cron, la fiecare 5 min). E independent de
run_price_monitor, deci detectează exact cazul în care acela a murit silențios.

Semnal de prospețime: max(fetchtime din cache, mtime fișier). Dacă vârsta depășește
WATCHDOG_STALE_MINUTES → trimite o alertă (ntfy + email), cu cooldown ca să nu spameze.

Variabile de mediu (din .env):
  PHONE_ALERT_URL / NTFY_TOPIC   — canal push (ca run_price_monitor)
  SMTP_USERNAME / SMTP_PASSWORD / ALERT_TO_EMAIL — email (opțional)
  WATCHDOG_STALE_MINUTES      (default 20)
  WATCHDOG_COOLDOWN_MINUTES   (default 60)
  WATCHDOG_CACHE_FILE         (default cache_prices_multi.json)
"""
import os
import sys
import json
import time
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
# Cache-ul stă în subfolderul cachedb/ (BINANCE_CACHE_DIR îl poate suprascrie).
_CACHE_DIR = Path(os.environ.get("BINANCE_CACHE_DIR", BASE_DIR / "cachedb"))
CACHE_FILE = Path(os.environ.get("WATCHDOG_CACHE_FILE", _CACHE_DIR / "cache_prices_multi.json"))
STATE_FILE = BASE_DIR / ".watchdog_state.json"
STALE_MINUTES = float(os.environ.get("WATCHDOG_STALE_MINUTES", "20"))
COOLDOWN_MINUTES = float(os.environ.get("WATCHDOG_COOLDOWN_MINUTES", "60"))


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
    """Verifică o dată. Întoarce True dacă a trimis alertă (cache stale + nu în cooldown)."""
    now = now if now is not None else time.time()
    freshness, detail = cache_freshness_seconds(CACHE_FILE)
    age_min = (now - freshness) / 60.0 if freshness > 0 else float("inf")
    is_stale = age_min > STALE_MINUTES

    if not is_stale:
        print(f"[watchdog] OK — cache proaspăt ({age_min:.1f} min < {STALE_MINUTES:.0f} min)")
        return False

    # cooldown: nu re-alarma prea des
    state = _load_state()
    last = state.get("last_alert_ts", 0)
    if (now - last) < COOLDOWN_MINUTES * 60:
        print(f"[watchdog] STALE ({age_min:.1f} min) dar în cooldown — nu re-alarmez")
        return False

    last_str = (datetime.fromtimestamp(freshness).strftime("%Y-%m-%d %H:%M:%S")
                if freshness > 0 else "necunoscut")
    age_txt = f"{age_min:.0f} min" if age_min != float("inf") else "∞"
    title = "⚠️ Price monitor OPRIT"
    message = (f"Cache-ul de prețuri e STALE: ultima actualizare acum {age_txt} "
               f"(la {last_str}). Detaliu: {detail}. "
               f"Probabil run_price_monitor.py s-a oprit — repornește-l.")
    print(f"[watchdog] ALARMĂ: {message}")
    _send_ntfy(title, message)
    _send_email(title, message)
    state["last_alert_ts"] = now
    _save_state(state)
    return True


if __name__ == "__main__":
    sent = check_once()
    sys.exit(2 if sent else 0)
