# alert_notifiers.py
import requests
import os
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

# Import your modules
import log

BASE_DIR = Path(__file__).resolve().parent


class AlertNotifier:

    def check_alert(condition, message, alert_interval=60):
        pass  # Placeholder for alert checking logic, can be implemented as needed
    
    @staticmethod
    def format_human_readable_time(value) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value).strftime("%m-%d %H:%M:%S")
            except Exception:
                return str(value)
        if hasattr(value, "strftime"):
            try:
                return value.strftime("%m-%d %H:%M:%S")
            except Exception:
                return str(value)
        return str(value)

    @staticmethod
    def is_new_coin_alert(alert: Any) -> bool:
        return isinstance(alert, dict) and alert.get("type") == "new_coin_discovered"

    @staticmethod
    def alert_symbol(alert: Any) -> str:
        """Simbolul, indiferent dacă alert e obiect PriceAlert sau dict (monedă nouă)."""
        if isinstance(alert, dict):
            return alert.get("symbol", "N/A")
        return getattr(alert, "symbol", "N/A")

    @staticmethod
    def utf8_header(value: str) -> str:
        """Valoare de header HTTP care păstrează caractere non-ASCII (ex. simbol '小蝌蚪').
        Header-ele sunt latin-1, dar ntfy decodează valoarea ca UTF-8 → trecem octeții
        UTF-8 prin latin-1 (passthrough). Așa simbolurile non-ASCII ajung intacte."""
        return value.encode("utf-8").decode("latin-1")

    @staticmethod
    def format_new_coin_message(alert: dict) -> str:
        lines = [
            f"🆕: {alert.get('symbol', 'N/A')} - {alert.get('name', alert.get('symbol', 'N/A'))}",
            f"Source: {alert.get('source', 'unknown')}",
            f"Added: {AlertNotifier.format_human_readable_time(alert.get('added_at'))}",
            f"Price: ${alert.get('price', 0):.4f}" if alert.get('price') is not None else "Price: N/A",
        ]
        url = alert.get("url")
        if url:
            lines.append(f"Link: {url}")
        return "\n".join(lines)

    @staticmethod
    def format_batch_message(alerts) -> str:
        # listează simbolurile separate prin virgulă pe prima linie
        #symbols = ", ".join(alert.symbol for alert in alerts)
        #lines = [f"({len(alerts)}): {symbols}",    "",]
        lines = []
        for alert in alerts:
            if AlertNotifier.is_new_coin_alert(alert):
                lines.append(AlertNotifier.format_new_coin_message(alert))
                continue

            direction = "U" if alert.alert_type == "up" else "D"
            reference_time = AlertNotifier.format_human_readable_time(
                getattr(alert, "reference_time", None) or getattr(alert, "timestamp", None)
            )

            lines.append(
                f"{alert.symbol}: {direction} {alert.percent_change:+.2f}% "
                f"| C ${alert.current_price:.4f} | R ${alert.reference_price:.4f} "
                f"({reference_time})"
            )

            url = getattr(alert, "url", None)
            if url:
                lines.append(f"Link: {url}")

        return "\n".join(lines)

    @staticmethod
    def print_to_console(alert):
        print("\n" + "=" * 70)
        print(str(alert))
        print("=" * 70)

    @staticmethod
    def save_to_file(alert, filename="alerts.log"):
        alert_file = Path(filename)
        if not alert_file.is_absolute():
            alert_file = BASE_DIR / alert_file
        try:
            with alert_file.open("a", encoding="utf-8") as f:
                if AlertNotifier.is_new_coin_alert(alert):
                    f.write(f"[{datetime.now().isoformat()}] NEW COIN {alert.get('symbol')} "
                            f"(source: {alert.get('source', 'unknown')})\n")
                    f.write(AlertNotifier.format_new_coin_message(alert) + "\n")
                    f.write("-" * 50 + "\n")
                    return True
                reference_time = AlertNotifier.format_human_readable_time(
                    getattr(alert, "reference_time", None) or getattr(alert, "timestamp", None)
                )
                f.write(f"[{datetime.now().isoformat()}] {alert.symbol} - {alert.alert_type} - {alert.percent_change:+.2f}%\n")
                f.write(f"  Price: ${alert.current_price:.4f}\n")
                f.write(f"  Reference: ${alert.reference_price:.4f} (at {reference_time})\n")
                url = getattr(alert, "url", None)
                if url:
                    f.write(f"  Link: {url}\n")
                f.write("-" * 50 + "\n")
            return True
        except Exception as e:
            print(f"[Notifier] File exception: {e}")
            return False

    @staticmethod
    def send_email_batch(alerts, email_config: Optional[dict] = None):
        email_config = email_config or {}
        smtp_server = email_config.get("smtp_server") or os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(email_config.get("smtp_port") or os.environ.get("SMTP_PORT", "587"))
        smtp_username = email_config.get("smtp_username") or os.environ.get("SMTP_USERNAME")
        smtp_password = email_config.get("smtp_password") or os.environ.get("SMTP_PASSWORD")
        to_email = email_config.get("to_email") or os.environ.get("ALERT_TO_EMAIL")

        if not alerts:
            print("[Notifier] Email: no alerts to send")
            return False

        if not smtp_username or not smtp_password or not to_email:
            print("[Notifier] Email: SMTP_USERNAME, SMTP_PASSWORD, and ALERT_TO_EMAIL are required")
            return False

        symbols = ", ".join(AlertNotifier.alert_symbol(alert) for alert in alerts)
        subject = f"CryptoAlerts: {len(alerts)} symbols ({symbols})"
        body = AlertNotifier.format_batch_message(alerts)
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = smtp_username
        msg["To"] = to_email
        msg["Subject"] = subject

        try:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.sendmail(smtp_username, [to_email], msg.as_string())
            return True
        except Exception as e:
            print(f"[Notifier] Email batch exception: {e}")
            return False

    @staticmethod
    def send_phone_webhook_batch(alerts, webhook_url: Optional[str] = None):
        if not alerts:
            print("[Notifier] Phone webhook: no alerts to send")
            return False

        print(f"[Notifier] Phone webhook batch for {len(alerts)} alert(s)")
        webhook_url = webhook_url or os.environ.get("PHONE_ALERT_URL")
        if not webhook_url and os.environ.get("NTFY_TOPIC"):
            webhook_url = f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}"
        if not webhook_url:
            print("[Notifier] Phone webhook: PHONE_ALERT_URL or NTFY_TOPIC is missing")
            return False

        symbols = ", ".join(AlertNotifier.alert_symbol(alert) for alert in alerts)
        title = f"({len(alerts)}): {symbols}"

        message = AlertNotifier.format_batch_message(alerts)
        tags = "chart_with_upwards_trend"
        payload = {"title": title, "message": message}

        try:
            if "ntfy.sh/" in webhook_url:
                # ntfy decodează Title ca UTF-8 → păstrăm simbolurile non-ASCII (ex. '小蝌蚪').
                response = requests.post(
                    webhook_url,
                    data=message.encode("utf-8"),
                    headers={
                        "Title": AlertNotifier.utf8_header(title),
                        "Priority": os.environ.get("NTFY_PRIORITY", "high"),
                        "Tags": tags,
                    },
                    timeout=10,
                )
                if response.status_code >= 400:
                    print(f"[Notifier] ntfy batch error: {response.status_code} {response.text}")
                    return False
                print(f"[Notifier] ntfy batch sent successfully for {len(alerts)} symbols")
                return True

            response = requests.post(webhook_url, json=payload, timeout=10)
            if response.status_code >= 400:
                print(f"[Notifier] Phone webhook batch error: {response.status_code} {response.text}")
                return False
            print(f"[Notifier] Phone webhook batch sent successfully for {len(alerts)} symbols")
            return True
        except Exception as e:
            print(f"[Notifier] Phone webhook batch exception: {e}")
            return False


    #Combined handler that sends alerts through multiple channels.    
    @staticmethod
    def send(alert, enable_console=True, enable_file=True, 
             enable_email=False, enable_phone_webhook=False):
        alerts = [alert] if not isinstance(alert, list) else alert
        if enable_console:
            for item in alerts:
                AlertNotifier.print_to_console(item)
        if enable_file:
            for item in alerts:
                AlertNotifier.save_to_file(item)
        if enable_email:
            AlertNotifier.send_email_batch(alerts)
        if enable_phone_webhook:
            AlertNotifier.send_phone_webhook_batch(alerts)


_URGENT_MARKERS = ("🛑", "🛡", "LICHID", "STOP-LOSS", "STOP_LOSS", "TRAILING", "ESUAT",
                   "MANUAL", "ERORI", "CATASTROF", "CRASH", "DISPARUT", "DEZECHILIBR")
# NOTA: fara 📉 (folosit si de alerta INFORMATIVA de pierdere '📉 SPCX -8%') si fara ⚠ singur
# (prea larg). Trailing-ul e prins de cuvantul 'TRAILING'. Urgentele DN (⚠ ...) au si LICHID/
# ERORI/MANUAL in titlu -> tot prinse. Vezi mai jos: overridezi cu email=True/False la nevoie.


def notify(title: str, body: str, source: str, symbol: str,
           price: Optional[float] = None, desktop: bool = False,
           email: Optional[bool] = None) -> None:
    """Wrapper PARTAJAT de notificare — folosit de flota (Binance) SI de kraken/HL/212.
    Clopotel terminal + alerta pe ntfy (mereu) + email (DOAR daca email=True) + desktop, prin
    AlertNotifier. `symbol` = eticheta activului, rezolvata de APELANT. O notificare esuata NU
    intrerupe trading-ul (try/except). Foloseste print() ca sa mearga si pe flota (log.py
    captureaza print) si pe boti (python3 -> stdout in .log-ul lor).

    email=True DOAR pt URGENTE (stop-loss/trailing/crash/liq/erori) — informativele (fill-uri,
    'X disponibil', alerte pret) merg doar pe ntfy, ca sa nu inunde email-ul.

    Extras din wrapper-ele duplicate kraken/notify.py, hyperliquid/notify.py, 212trading/ipo_notify.py.
    """
    for _ in range(5):
        sys.stdout.write("\a")
        sys.stdout.flush()
        time.sleep(0.2)
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
    try:
        AlertNotifier.send_phone_webhook_batch([alert], webhook_url=ntfy_url)
    except Exception as e:  # noqa: BLE001
        print(f"  ! notify ntfy esuat: {e}")
    if email is None:   # auto: email DOAR daca titlul are un marker de urgenta (nu la fill-uri/'disponibil'/pret)
        email = any(m in title.upper() for m in _URGENT_MARKERS)
    if email and os.environ.get("ALERT_TO_EMAIL"):
        try:
            AlertNotifier.send_email_batch([alert])
        except Exception as e:  # noqa: BLE001
            print(f"  ! notify email esuat: {e}")
    if desktop:
        try:
            subprocess.run(["notify-send", "-u", "critical", title, body], check=False)
        except (FileNotFoundError, OSError):
            pass
