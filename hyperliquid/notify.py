#!/usr/bin/env python3
"""notify.py — ntfy + email prin AlertNotifier-ul partajat din radacina proiectului."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alertnotifiers import AlertNotifier
from common import log


def notify(title: str, body: str, source: str,
           price: float | None = None, desktop: bool = False) -> None:
    for _ in range(5):
        sys.stdout.write("\a")
        sys.stdout.flush()
        time.sleep(0.2)

    symbol = os.environ.get("SYMBOL_LABEL") or os.environ.get("HL_COIN") or "HL"
    alert = {
        "type": "new_coin_discovered",
        "symbol": symbol,
        "name": title,
        "source": source,
        "price": price,
        "added_at": datetime.now(),
        "url": None,
    }
    ntfy_topic = os.environ.get("NTFY_TOPIC")
    ntfy_url = f"https://ntfy.sh/{ntfy_topic}" if ntfy_topic else None
    AlertNotifier.send_phone_webhook_batch([alert], webhook_url=ntfy_url)
    if os.environ.get("ALERT_TO_EMAIL"):
        AlertNotifier.send_email_batch([alert])
    if desktop:
        try:
            subprocess.run(["notify-send", "-u", "critical", title, body], check=False)
        except FileNotFoundError:
            pass
