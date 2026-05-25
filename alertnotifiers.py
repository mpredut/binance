# alert_notifiers.py
import requests
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

# Import your modules
import log

BASE_DIR = Path(__file__).resolve().parent


class AlertNotifier:
    """Class for sending alerts through multiple channels."""

    def check_alert(condition, message, alert_interval=60):
        pass  # Placeholder for alert checking logic, can be implemented as needed
    
    @staticmethod
    def format_human_readable_time(value) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return str(value)
        if hasattr(value, "strftime"):
            try:
                return value.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return str(value)
        return str(value)

    @staticmethod
    def is_new_coin_alert(alert: Any) -> bool:
        return isinstance(alert, dict) and alert.get("type") == "new_coin_discovered"

    @staticmethod
    def format_new_coin_message(alert: dict) -> str:
        lines = [
            f"🆕 NEW COIN: {alert.get('symbol', 'N/A')} - {alert.get('name', alert.get('symbol', 'N/A'))}",
            f"Source: {alert.get('source', 'unknown')}",
            f"Added: {AlertNotifier.format_human_readable_time(alert.get('added_at'))}",
            f"Price: ${alert.get('price', 0):.6f}" if alert.get('price') is not None else "Price: N/A",
        ]
        url = alert.get("url")
        if url:
            lines.append(f"Link: {url}")
        return "\n".join(lines)

    @staticmethod
    def format_batch_message(alerts) -> str:
        lines = [
            f"Crypto alerts: {len(alerts)} items",
            "",
        ]
        for alert in alerts:
            if AlertNotifier.is_new_coin_alert(alert):
                lines.append(AlertNotifier.format_new_coin_message(alert))
                continue

            direction = "RISE" if alert.alert_type == "up" else "DROP"
            reference_time = AlertNotifier.format_human_readable_time(
                getattr(alert, "reference_time", None) or getattr(alert, "timestamp", None)
            )
            lines.append(
                f"{alert.symbol}: {direction} {alert.percent_change:+.2f}% "
                f"| price ${alert.current_price:.8f} | reference ${alert.reference_price:.8f} "
                f"(at {reference_time})"
            )
            url = getattr(alert, "url", None)
            if url:
                lines.append(f"Link: {url}")
        return "\n".join(lines)

    @staticmethod
    def print_to_console(alert):
        """Print the alert to the console."""
        print("\n" + "=" * 70)
        print(str(alert))
        print("=" * 70)

    @staticmethod
    def save_to_file(alert, filename="alerts.log"):
        """Save the alert to a file."""
        alert_file = Path(filename)
        if not alert_file.is_absolute():
            alert_file = BASE_DIR / alert_file
        try:
            reference_time = AlertNotifier.format_human_readable_time(
                getattr(alert, "reference_time", None) or getattr(alert, "timestamp", None)
            )
            with alert_file.open("a", encoding="utf-8") as f:
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
    def send_telegram(alert, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        """Send an alert through Telegram."""
        bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

        if not bot_token or not chat_id:
            print("[Notifier] Telegram: bot token or chat_id is missing")
            return

        if AlertNotifier.is_new_coin_alert(alert):
            message = (
                "🆕 *New Coin Alert* 🆕\n\n"
                f"*{alert.get('symbol', 'N/A')}* - {alert.get('name', alert.get('symbol', 'N/A'))}\n"
                f"Source: {alert.get('source', 'unknown')}\n"
                f"Added: {AlertNotifier.format_human_readable_time(alert.get('added_at'))}\n"
                f"Price: `{alert.get('price', 0):.6f}`\n"
                f"Link: {alert.get('url', 'N/A')}"
            )
        else:
            reference_time = AlertNotifier.format_human_readable_time(
                getattr(alert, "reference_time", None) or getattr(alert, "timestamp", None)
            )
            message = (
                f"🚨 *Crypto Alert* 🚨\n\n"
                f"*{alert.symbol}*\n"
                f"{'🟢 RISE' if alert.alert_type == 'up' else '🔴 DROP'}\n\n"
                f"Current price: `${alert.current_price:.4f}`\n"
                f"Reference: `${alert.reference_price:.4f}` (at {reference_time})\n"
                f"Change: `{alert.percent_change:+.2f}%`\n"
                f"Threshold: `{alert.threshold}%`"
            )
            url = getattr(alert, "url", None)
            if url:
                message += f"\nLink: {url}"

        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                print(f"[Notifier] Telegram error: {response.text}")
        except Exception as e:
            print(f"[Notifier] Telegram exception: {e}")

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

        subject = f"Crypto alerts: {len(alerts)} symbols"
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

        title = f"Crypto alerts: {len(alerts)} symbols"
        message = AlertNotifier.format_batch_message(alerts)
        tags = "chart_with_upwards_trend"
        payload = {"title": title, "message": message}

        try:
            if "ntfy.sh/" in webhook_url:
                response = requests.post(
                    webhook_url,
                    data=message.encode("utf-8"),
                    headers={
                        "Title": title,
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

    @staticmethod
    def combined_handler(alert, enable_console=True, enable_file=True, enable_telegram=False, enable_email=False, enable_phone_webhook=False):
        """Combined handler that sends alerts through multiple channels."""
        alerts = [alert] if not isinstance(alert, list) else alert
        if enable_console:
            for item in alerts:
                AlertNotifier.print_to_console(item)
        if enable_file:
            for item in alerts:
                AlertNotifier.save_to_file(item)
        if enable_telegram:
            for item in alerts:
                AlertNotifier.send_telegram(item)
        if enable_email:
            AlertNotifier.send_email_batch(alerts)
        if enable_phone_webhook:
            AlertNotifier.send_phone_webhook_batch(alerts)
