#!/usr/bin/env python3
"""
ipo_notify.py — strat subtire peste AlertNotifier (ntfy + email) pentru watcher-ul SPCX.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

# Add parent directory to path to import alertnotifiers
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alertnotifiers import AlertNotifier
from ipo_common import log


def notify(title: str, body: str, source: str,
           price: float | None = None, desktop: bool = False,
           symbol: str | None = None) -> None:
    """Trimite o notificare pe toate canalele active (terminal bell, ntfy, email, desktop).

    `symbol` explicit e preferat (multi-activ intr-un proces) fata de os.environ,
    care e global si ar da simbolul gresit cand ruleaza mai multe active deodata.
    """
    # 1) clopotel terminal
    for _ in range(5):
        sys.stdout.write("\a")
        sys.stdout.flush()
        time.sleep(0.2)

    symbol = symbol or os.environ.get("SYMBOL_LABEL") or os.environ.get("YAHOO_SYMBOL") or "STOCK"
    alert = {
        "type": "new_coin_discovered",
        "symbol": symbol,
        "name": title,
        "source": source,
        "price": price,
        "added_at": datetime.now(),
        "url": None,
    }

    # 2) ntfy (NTFY_TOPIC din .env)
    ntfy_topic = os.environ.get("NTFY_TOPIC")
    ntfy_url = f"https://ntfy.sh/{ntfy_topic}" if ntfy_topic else None
    AlertNotifier.send_phone_webhook_batch([alert], webhook_url=ntfy_url)

    # 3) email (automat daca ALERT_TO_EMAIL e configurat)
    if os.environ.get("ALERT_TO_EMAIL"):
        AlertNotifier.send_email_batch([alert])

    # 4) desktop (optional)
    if desktop:
        try:
            subprocess.run(["notify-send", "-u", "critical", title, body], check=False)
        except FileNotFoundError:
            pass  # notify-send indisponibil (WSL fara X11)
