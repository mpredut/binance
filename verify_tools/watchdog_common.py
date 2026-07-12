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
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # verify_tools/ -> radacina repo


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
    url = os.environ.get("PHONE_ALERT_URL")
    if not url and os.environ.get("NTFY_TOPIC"):
        url = f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}"
    if not url:
        print("[watchdog] fara PHONE_ALERT_URL/NTFY_TOPIC — sar push-ul")
        return False
    try:
        import requests
        # ntfy decodeaza Title ca UTF-8 -> trecem octetii UTF-8 prin latin-1, ca sa
        # pastram caractere non-ASCII (emoji, simboluri) in titlu.
        utf8_title = title.encode("utf-8").decode("latin-1")
        if "ntfy.sh/" in url:
            r = requests.post(url, data=message.encode("utf-8"),
                              headers={"Title": utf8_title, "Priority": "urgent",
                                       "Tags": "warning"}, timeout=10)
        else:
            r = requests.post(url, json={"title": title, "message": message}, timeout=10)
        ok = r.status_code < 400
        print(f"[watchdog] push {'OK' if ok else 'ESUAT ' + str(r.status_code)}")
        return ok
    except Exception as e:
        print(f"[watchdog] push exceptie: {e}")
        return False


def send_email(subject, body):
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
        print(f"[watchdog] email exceptie: {e}")
        return False


def alert(title, message):
    """Trimite push + email. Intoarce True daca cel putin unul a reusit."""
    ok_push = send_ntfy(title, message)
    ok_mail = send_email(title, message)
    return bool(ok_push or ok_mail)
