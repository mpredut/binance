# alert_notifiers.py
import requests
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

# Importă modulele tale
import log

DEFAULT_TO_EMAIL = "tuderp@gmail.com"
BASE_DIR = Path(__file__).resolve().parent


class AlertNotifier:
    """Clasă pentru trimiterea alertelor prin diverse canale"""

    @staticmethod
    def format_plain_message(alert) -> str:
        direction = "CRESTERE" if alert.alert_type == "up" else "SCADERE"
        return (
            f"Alerta Crypto: {direction}\n"
            f"Simbol: {alert.symbol}\n"
            f"Pret curent: ${alert.current_price:.8f}\n"
            f"Referinta: ${alert.reference_price:.8f}\n"
            f"Variatie: {alert.percent_change:+.2f}%\n"
            f"Prag: {alert.threshold}%\n"
            f"Timp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    @staticmethod
    def format_batch_message(alerts) -> str:
        lines = [
            f"Alerte Crypto: {len(alerts)} simboluri",
            f"Timp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        for alert in alerts:
            direction = "CRESTERE" if alert.alert_type == "up" else "SCADERE"
            lines.append(
                f"{alert.symbol}: {direction} {alert.percent_change:+.2f}% "
                f"| pret ${alert.current_price:.8f} | ref ${alert.reference_price:.8f}"
            )
        return "\n".join(lines)
    
    @staticmethod
    def print_to_console(alert):
        """Afișează alerta în consolă"""
        print("\n" + "=" * 70)
        print(str(alert))
        print("=" * 70)
    
    @staticmethod
    def save_to_file(alert, filename="alerts.log"):
        """Salvează alerta într-un fișier"""
        alert_file = Path(filename)
        if not alert_file.is_absolute():
            alert_file = BASE_DIR / alert_file
        try:
            with alert_file.open("a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {alert.symbol} - {alert.alert_type} - {alert.percent_change:+.2f}%\n")
                f.write(f"  Preț: ${alert.current_price:.4f}\n")
                f.write(f"  Referință: ${alert.reference_price:.4f}\n")
                f.write("-" * 50 + "\n")
            return True
        except Exception as e:
            print(f"[Notifier] File excepție: {e}")
            return False
    
    @staticmethod
    def send_telegram(alert, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        """Trimite alertă prin Telegram"""
        bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        
        if not bot_token or not chat_id:
            print("[Notifier] Telegram: token sau chat_id lipsă")
            return
        
        message = (
            f"🚨 *Alertă Crypto* 🚨\n\n"
            f"*{alert.symbol}*\n"
            f"{'🟢 CREȘTERE' if alert.alert_type == 'up' else '🔴 SCĂDERE'}\n\n"
            f"Preț curent: `${alert.current_price:.4f}`\n"
            f"Referință: `${alert.reference_price:.4f}`\n"
            f"Variație: `{alert.percent_change:+.2f}%`\n"
            f"Prag: `{alert.threshold}%`"
        )
        
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                print(f"[Notifier] Telegram eroare: {response.text}")
        except Exception as e:
            print(f"[Notifier] Telegram excepție: {e}")
    
    @staticmethod
    def send_email(alert, email_config: Optional[dict] = None):
        """Trimite alertă prin email (necesită configurare SMTP)"""
        email_config = email_config or {}
        smtp_server = email_config.get("smtp_server") or os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(email_config.get("smtp_port") or os.environ.get("SMTP_PORT", "587"))
        smtp_username = email_config.get("smtp_username") or os.environ.get("SMTP_USERNAME")
        smtp_password = email_config.get("smtp_password") or os.environ.get("SMTP_PASSWORD")
        to_email = email_config.get("to_email") or os.environ.get("ALERT_TO_EMAIL", DEFAULT_TO_EMAIL)

        if not smtp_username or not smtp_password:
            print("[Notifier] Email: SMTP_USERNAME sau SMTP_PASSWORD lipsă")
            return False

        subject = f"Crypto alert: {alert.symbol} {alert.percent_change:+.2f}%"
        body = AlertNotifier.format_plain_message(alert)
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
            print(f"[Notifier] Email excepție: {e}")
            return False

    @staticmethod
    def send_email_batch(alerts, email_config: Optional[dict] = None):
        email_config = email_config or {}
        smtp_server = email_config.get("smtp_server") or os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(email_config.get("smtp_port") or os.environ.get("SMTP_PORT", "587"))
        smtp_username = email_config.get("smtp_username") or os.environ.get("SMTP_USERNAME")
        smtp_password = email_config.get("smtp_password") or os.environ.get("SMTP_PASSWORD")
        to_email = email_config.get("to_email") or os.environ.get("ALERT_TO_EMAIL", DEFAULT_TO_EMAIL)

        if not smtp_username or not smtp_password:
            print("[Notifier] Email: SMTP_USERNAME sau SMTP_PASSWORD lipsă")
            return False

        subject = f"Crypto alerts: {len(alerts)} simboluri"
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
            print(f"[Notifier] Email batch excepție: {e}")
            return False

    @staticmethod
    def send_phone_webhook(alert, webhook_url: Optional[str] = None):
        """Trimite alertă către un webhook de telefon (ex: Tasker/ntfy/IFTTT)."""
        webhook_url = webhook_url or os.environ.get("PHONE_ALERT_URL")
        if not webhook_url and os.environ.get("NTFY_TOPIC"):
            webhook_url = f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}"
        if not webhook_url:
            print("[Notifier] Phone webhook: PHONE_ALERT_URL sau NTFY_TOPIC lipsă")
            return False

        try:
            if "ntfy.sh/" in webhook_url:
                response = requests.post(
                    webhook_url,
                    data=AlertNotifier.format_plain_message(alert).encode("utf-8"),
                    headers={
                        "Title": f"Crypto alert: {alert.symbol}",
                        "Priority": os.environ.get("NTFY_PRIORITY", "high"),
                        "Tags": "chart_with_upwards_trend" if alert.alert_type == "up" else "chart_with_downwards_trend",
                    },
                    timeout=10,
                )
                if response.status_code >= 400:
                    print(f"[Notifier] ntfy eroare: {response.status_code} {response.text}")
                    return False
                return True

            response = requests.post(
                webhook_url,
                json={
                    "title": f"Crypto alert: {alert.symbol}",
                    "message": AlertNotifier.format_plain_message(alert),
                    "symbol": alert.symbol,
                    "alert_type": alert.alert_type,
                    "current_price": alert.current_price,
                    "percent_change": alert.percent_change,
                },
                timeout=10,
            )
            if response.status_code >= 400:
                print(f"[Notifier] Phone webhook eroare: {response.status_code} {response.text}")
                return False
            return True
        except Exception as e:
            print(f"[Notifier] Phone webhook excepție: {e}")
            return False

    @staticmethod
    def send_phone_webhook_batch(alerts, webhook_url: Optional[str] = None):
        webhook_url = webhook_url or os.environ.get("PHONE_ALERT_URL")
        if not webhook_url and os.environ.get("NTFY_TOPIC"):
            webhook_url = f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}"
        if not webhook_url:
            print("[Notifier] Phone webhook: PHONE_ALERT_URL sau NTFY_TOPIC lipsă")
            return False

        message = AlertNotifier.format_batch_message(alerts)
        try:
            if "ntfy.sh/" in webhook_url:
                response = requests.post(
                    webhook_url,
                    data=message.encode("utf-8"),
                    headers={
                        "Title": f"Crypto alerts: {len(alerts)} simboluri",
                        "Priority": os.environ.get("NTFY_PRIORITY", "high"),
                        "Tags": "chart_with_upwards_trend",
                    },
                    timeout=10,
                )
                if response.status_code >= 400:
                    print(f"[Notifier] ntfy batch eroare: {response.status_code} {response.text}")
                    return False
                return True

            response = requests.post(
                webhook_url,
                json={"title": f"Crypto alerts: {len(alerts)} simboluri", "message": message},
                timeout=10,
            )
            if response.status_code >= 400:
                print(f"[Notifier] Phone webhook batch eroare: {response.status_code} {response.text}")
                return False
            return True
        except Exception as e:
            print(f"[Notifier] Phone webhook batch excepție: {e}")
            return False
    
    @staticmethod
    def combined_handler(alert, enable_console=True, enable_file=True, enable_telegram=False, enable_email=False, enable_phone_webhook=False):
        """Handler combinat care trimite pe mai multe canale"""
        if enable_console:
            AlertNotifier.print_to_console(alert)
        if enable_file:
            AlertNotifier.save_to_file(alert)
        if enable_telegram:
            AlertNotifier.send_telegram(alert)
        if enable_email:
            AlertNotifier.send_email(alert)
        if enable_phone_webhook:
            AlertNotifier.send_phone_webhook(alert)
