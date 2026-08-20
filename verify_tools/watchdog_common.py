#!/usr/bin/env python3
"""watchdog_common.py — infrastructura PARTAJATA de alertare pentru watchdog-uri
(watchdogfor_cache, watchdogfor_anomaly): incarca env-ul, trimite push (ntfy) + email,
si tine starea de cooldown. Extras din watchdogfor_cache ca sa nu duplicam (DRY).

Variabile de mediu (din .env / config.env din radacina):
  PHONE_ALERT_URL / NTFY_TOPIC                    — canal push
  SMTP_USERNAME / SMTP_PASSWORD / ALERT_TO_EMAIL  — email (optional)
  SMTP_SERVER / SMTP_PORT                         — server email (default gmail:587)
"""
import os
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent      # verify_tools/ -> radacina repo
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alertnotifiers import AlertNotifier  # noqa: E402


def _watchdog_event(title, message):
    return {
        "type": "bot_event", "symbol": "SYSTEM", "name": title,
        "source": "watchdog", "body": message, "url": None,
    }


def load_env():
    """Incarca .env (secrete gitignored) + config.env (versionat) din radacina."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        load_dotenv(ROOT / "config.env")
    except Exception:
        pass


def load_state(state_file):
    try:
        return json.load(open(state_file))
    except Exception:
        return {}


def save_state(state_file, state):
    try:
        tmp = str(state_file) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, state_file)
    except Exception as e:
        print(f"[watchdog] nu pot scrie state: {e}")


def send_ntfy(title, message):
    # watchdog = categoria ERROR -> prefera topic-ul dedicat; fallback PHONE_ALERT_URL / NTFY_TOPIC
    topic = os.environ.get("NTFY_TOPIC_ERROR")
    url = (f"https://ntfy.sh/{topic}" if topic else None) or os.environ.get("PHONE_ALERT_URL")
    if not url and os.environ.get("NTFY_TOPIC"):
        url = f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}"
    if not url:
        print("[watchdog] fara PHONE_ALERT_URL/NTFY_TOPIC — sar push-ul")
        return False
    ok = AlertNotifier.send_phone_webhook_batch([_watchdog_event(title, message)], webhook_url=url)
    print(f"[watchdog] push {'OK' if ok else 'ESUAT'}")
    return ok


def send_email(subject, body):
    ok = AlertNotifier.send_email_batch(
        [_watchdog_event(subject, body)], subject=subject,
    )
    print(f"[watchdog] email {'trimis' if ok else 'omis/esuat'}")
    return ok


def alert(title, message):
    """Trimite push + email. Intoarce True daca cel putin unul a reusit."""
    ok_push = send_ntfy(title, message)
    ok_mail = send_email(title, message)
    return bool(ok_push or ok_mail)
