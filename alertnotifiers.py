# alert_notifiers.py
import requests
import os
from datetime import datetime
from typing import Optional

# Importă modulele tale
import log

class AlertNotifier:
    """Clasă pentru trimiterea alertelor prin diverse canale"""
    
    @staticmethod
    def print_to_console(alert):
        """Afișează alerta în consolă"""
        print("\n" + "=" * 70)
        print(str(alert))
        print("=" * 70)
    
    @staticmethod
    def save_to_file(alert, filename="alerts.log"):
        """Salvează alerta într-un fișier"""
        with open(filename, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] {alert.symbol} - {alert.alert_type} - {alert.percent_change:+.2f}%\n")
            f.write(f"  Preț: ${alert.current_price:.4f}\n")
            f.write(f"  Referință: ${alert.reference_price:.4f}\n")
            f.write("-" * 50 + "\n")
    
    @staticmethod
    def send_telegram(alert, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        """Trimite alertă prin Telegram"""
        bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        
        if not bot_token or not chat_id:
            log.print("[Notifier] Telegram: token sau chat_id lipsă")
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
                log.print(f"[Notifier] Telegram eroare: {response.text}")
        except Exception as e:
            log.print(f"[Notifier] Telegram excepție: {e}")
    
    @staticmethod
    def send_email(alert, email_config: Optional[dict] = None):
        """Trimite alertă prin email (necesită configurare SMTP)"""
        # Implementare opțională - poți adăuga dacă ai nevoie
        pass
    
    @staticmethod
    def combined_handler(alert, enable_console=True, enable_file=True, enable_telegram=False):
        """Handler combinat care trimite pe mai multe canale"""
        if enable_console:
            AlertNotifier.print_to_console(alert)
        if enable_file:
            AlertNotifier.save_to_file(alert)
        if enable_telegram:
            AlertNotifier.send_telegram(alert)