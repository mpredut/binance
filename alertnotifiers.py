# alert_notifiers.py
from __future__ import annotations

import requests
import hashlib
import json
import os
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

# Import your modules
import log
from lock import FileLock

BASE_DIR = Path(__file__).resolve().parent

_URGENT_MARKERS = (
    "🛑", "🛡", "LICHID", "STOP-LOSS", "STOP_LOSS", "TRAILING", "ESUAT",
    "MANUAL", "ERORI", "CATASTROF", "CRASH", "DISPARUT", "DEZECHILIBR",
)
_GUARD_MARKERS = (
    "🛑", "🛡", "STOP-LOSS", "STOP_LOSS", "TRAILING", "LICHID", "CATASTROF", "CRASH",
)
_OPS_MARKERS = ("ESUAT", "ERORI", "MANUAL", "DISPARUT", "DEZECHILIBR")


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _delivery_state_path() -> Path:
    configured = os.environ.get("NOTIFICATION_STATE_FILE")
    return Path(configured).expanduser() if configured else BASE_DIR / "logs/notification_delivery_state.json"


def _load_delivery_state(path: Path, today: str) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        state = {}
    if state.get("date_utc") != today:
        state = {"schema_version": 1, "date_utc": today, "channels": {}}
    state.setdefault("channels", {})
    return state


def _save_delivery_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _alert_identity(alert: Any) -> dict:
    """Identitate stabilă: exclude timestamp-ul și prețurile care fluctuează la fiecare poll."""
    if isinstance(alert, dict):
        return {
            key: alert.get(key)
            for key in ("type", "symbol", "name", "source", "body", "url")
        }
    return {
        "type": alert.__class__.__name__,
        "symbol": getattr(alert, "symbol", None),
        "alert_type": getattr(alert, "alert_type", None),
        "threshold": getattr(alert, "threshold", None),
    }


def _delivery_fingerprint(alerts: list[Any]) -> str:
    identities = sorted(
        (json.dumps(_alert_identity(alert), sort_keys=True, default=str) for alert in alerts),
    )
    return hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()


def _alerts_are_urgent(alerts: list[Any]) -> bool:
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        title = str(alert.get("name") or alert.get("symbol") or "").upper()
        source = str(alert.get("source") or "").lower()
        if any(marker in title for marker in _URGENT_MARKERS) or "watchdog" in source:
            return True
    return False


def _dedup_seconds(alerts: list[Any], urgent: bool) -> int:
    titles = " ".join(
        str(alert.get("name") or "").upper()
        for alert in alerts if isinstance(alert, dict)
    )
    if "DISPONIBIL" in titles:
        return _positive_int_env("NOTIFICATION_STARTUP_DEDUP_SECONDS", 6 * 60 * 60)
    if urgent:
        return _positive_int_env("NOTIFICATION_URGENT_DEDUP_SECONDS", 5 * 60)
    if any(
        not isinstance(alert, dict) or alert.get("type") == "new_coin_discovered"
        for alert in alerts
    ):
        return _positive_int_env("NOTIFICATION_PRICE_DEDUP_SECONDS", 30 * 60)
    return _positive_int_env("NOTIFICATION_DEDUP_SECONDS", 15 * 60)


def _reserve_delivery(channel: str, alerts: list[Any], *, urgent: bool) -> tuple[bool, str, bool]:
    """Rezervă atomic o livrare între procese.

    Întoarce ``(allowed, reason, warn_once)``. Rezervarea este conservatoare: o
    încercare de rețea consumă bugetul local, fiindcă un timeout poate apărea după
    ce furnizorul a acceptat mesajul.
    """
    now = time.time()
    today = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    path = _delivery_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = _delivery_fingerprint(alerts)
    budget = _positive_int_env(
        "NTFY_DAILY_BUDGET" if channel == "ntfy" else "EMAIL_DAILY_BUDGET",
        100 if channel == "ntfy" else 40,
    )
    reserve = min(
        budget,
        _positive_int_env(
            "NTFY_URGENT_RESERVE" if channel == "ntfy" else "EMAIL_URGENT_RESERVE",
            20 if channel == "ntfy" else 10,
        ),
    )
    lock_path = path.with_suffix(path.suffix + ".lock")
    with FileLock(lock_path):
        state = _load_delivery_state(path, today)
        channel_state = state["channels"].setdefault(
            channel,
            {"sent": 0, "last": {}, "blocked": False, "budget_warning_sent": False},
        )
        if channel_state.get("blocked"):
            return False, "provider_daily_limit", False

        last = channel_state.setdefault("last", {})
        cutoff = now - 2 * 24 * 60 * 60
        channel_state["last"] = {
            key: value for key, value in last.items() if float(value) >= cutoff
        }
        previous = channel_state["last"].get(fingerprint)
        if previous is not None and now - float(previous) < _dedup_seconds(alerts, urgent):
            _save_delivery_state(path, state)
            return False, "duplicate", False

        sent = int(channel_state.get("sent", 0))
        allowed_count = budget if urgent else max(0, budget - reserve)
        if sent >= allowed_count:
            warn = not bool(channel_state.get("budget_warning_sent"))
            channel_state["budget_warning_sent"] = True
            _save_delivery_state(path, state)
            return False, "local_daily_budget", warn

        channel_state["sent"] = sent + 1
        channel_state["last"][fingerprint] = now
        _save_delivery_state(path, state)
        return True, "reserved", False


def _mark_provider_daily_limit(channel: str) -> bool:
    """Blochează canalul până la resetarea UTC și cere o singură alertă alternativă."""
    now = time.time()
    today = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    path = _delivery_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(path.suffix + ".lock")):
        state = _load_delivery_state(path, today)
        channel_state = state["channels"].setdefault(channel, {})
        first = not bool(channel_state.get("budget_warning_sent"))
        channel_state["blocked"] = True
        channel_state["budget_warning_sent"] = True
        _save_delivery_state(path, state)
        return first


def _is_provider_daily_limit(response: Any) -> bool:
    if getattr(response, "status_code", None) != 429:
        return False
    body = str(getattr(response, "text", "") or "").lower()
    return "42908" in body or "daily" in body


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
    def format_bot_event(alert: dict) -> str:
        """Corp COMPACT pt evenimente de bot (trade/guard/...): detaliul · platforma · timestamp.
        Numele (=actiunea) devine TITLUL ntfy. Platforma plain (se deduce). Timestamp scurt
        FARA an ('MM-DD HH:MM')."""
        body = (alert.get("body") or "").strip()
        head = body or alert.get("name", alert.get("symbol", "?"))
        parts = [head, str(alert.get("source", "?"))]
        ts = alert.get("added_at")
        if ts is not None:
            try:
                parts.append(ts.strftime("%m-%d %H:%M"))   # fara an
            except Exception:  # noqa: BLE001
                pass
        return " · ".join(parts)

    @staticmethod
    def format_batch_message(alerts) -> str:
        # listează simbolurile separate prin virgulă pe prima linie
        #symbols = ", ".join(alert.symbol for alert in alerts)
        #lines = [f"({len(alerts)}): {symbols}",    "",]
        lines = []
        for alert in alerts:
            if isinstance(alert, dict) and alert.get("type") == "bot_event":
                lines.append(AlertNotifier.format_bot_event(alert))
                continue
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
    def send_email_batch(
        alerts, email_config: Optional[dict] = None, subject: Optional[str] = None,
    ):
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

        alerts = list(alerts)
        urgent = _alerts_are_urgent(alerts)
        allowed, reason, _warn = _reserve_delivery("email", alerts, urgent=urgent)
        if not allowed:
            print(f"[Notifier] Email omis de politica de livrare: {reason}")
            return reason == "duplicate"

        symbols = ", ".join(AlertNotifier.alert_symbol(alert) for alert in alerts)
        subject = subject or f"CryptoAlerts: {len(alerts)} symbols ({symbols})"
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

        alerts = list(alerts)
        symbols = ", ".join(AlertNotifier.alert_symbol(alert) for alert in alerts)
        # pt un SINGUR eveniment de bot: titlul = actiunea (name); altfel '(N): simboluri'
        if len(alerts) == 1 and isinstance(alerts[0], dict) and alerts[0].get("type") == "bot_event":
            title = alerts[0].get("name") or symbols
        else:
            title = f"({len(alerts)}): {symbols}"

        message = AlertNotifier.format_batch_message(alerts)
        tags = "chart_with_upwards_trend"
        payload = {"title": title, "message": message}

        is_ntfy = "ntfy.sh/" in webhook_url
        if is_ntfy:
            urgent = _alerts_are_urgent(alerts)
            allowed, reason, warn = _reserve_delivery("ntfy", alerts, urgent=urgent)
            if not allowed:
                print(f"[Notifier] ntfy omis de politica de livrare: {reason}")
                if warn:
                    AlertNotifier._send_budget_warning("ntfy", reason)
                return reason == "duplicate"

        try:
            if is_ntfy:
                # ntfy decodează Title ca UTF-8 → păstrăm simbolurile non-ASCII (ex. '小蝌蚪').
                response = None
                for attempt in range(2):
                    response = requests.post(
                        webhook_url,
                        data=message.encode("utf-8"),
                        headers={
                            "Title": AlertNotifier.utf8_header(title),
                            "Priority": "urgent" if urgent else os.environ.get("NTFY_PRIORITY", "high"),
                            "Tags": "warning" if urgent else tags,
                        },
                        timeout=10,
                    )
                    if response.status_code != 429 or _is_provider_daily_limit(response):
                        break
                    if attempt == 0:
                        try:
                            wait = min(float(response.headers.get("Retry-After", 3) or 3), 8.0)
                        except (AttributeError, TypeError, ValueError):
                            wait = 3.0
                        print(f"[Notifier] ntfy 429 tranzitoriu; reincerc dupa {wait:.0f}s")
                        time.sleep(wait)
                if _is_provider_daily_limit(response):
                    if _mark_provider_daily_limit("ntfy"):
                        AlertNotifier._send_budget_warning("ntfy", "provider_daily_limit")
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
    def _send_budget_warning(channel: str, reason: str) -> None:
        """Fallback unic pe email; nu încearcă să compenseze fiecare mesaj suprimat."""
        alert = {
            "type": "bot_event", "symbol": "SYSTEM",
            "name": f"ERORI LIMITA {channel.upper()}", "source": "notifier",
            "body": (
                f"Canalul {channel} a fost oprit pentru restul zilei UTC: {reason}. "
                "Alertele urgente continuă pe celelalte canale disponibile."
            ),
            "added_at": datetime.now(),
        }
        AlertNotifier.send_email_batch(
            [alert], subject=f"Trading alerts: limita {channel} atinsa",
        )


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


def _topic_for(title: str, source: str) -> Optional[str]:
    """Ruteaza notificarea pe topic-ul ntfy potrivit categoriei (title + source):
      guard  = stop-loss/trailing/crash/lichidare (BANI in pericol)
      error  = erori/esuat/manual/pozitie disparuta/watchdog (SISTEM)
      price  = alerte de prag pret
      trades = fill-uri, 'X disponibil', DN rutina (deschis/inchis/funding) — restul.
    Citeste NTFY_TOPIC_<CAT>; fallback NTFY_TOPIC. Consistent cu email-ul (guard+error = urgent)."""
    t = title.upper(); s = (source or "").lower()
    if any(m in t for m in _GUARD_MARKERS):
        cat = "GUARD"
    elif any(m in t for m in _OPS_MARKERS) or "watchdog" in s:
        cat = "ERROR"
    elif "alert" in s or "prag" in t.lower() or "threshold" in t.lower():
        cat = "PRICE"
    else:
        cat = "TRADES"       # include DN de rutina (deschis/inchis/funding) — fara topic separat.
    return os.environ.get(f"NTFY_TOPIC_{cat}") or os.environ.get("NTFY_TOPIC")
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
        "type": "bot_event",          # eveniment de bot (trade/guard/etc.) — randare compacta,
        "symbol": symbol,             # NU sablonul de 'moneda noua' (🆕/Added) care ramane pt discovery
        "name": title,                # = actiunea (devine TITLUL ntfy)
        "source": source,
        "price": price,
        "body": body,                 # detaliul compact (o linie) construit de apelant
        "added_at": datetime.now(),
        "url": None,
    }
    ntfy_topic = _topic_for(title, source)          # rutare pe topic dupa categorie
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


def bind_notify(symbol_env_keys: tuple[str, ...], default_symbol: str):
    """Leagă notificatorul comun de convenția de simbol a unui venue.

    Wrapperul rezultat păstrează semnătura folosită de Kraken/HL/T212 și permite
    un ``symbol`` explicit pentru procesele multi-activ. Fișierele venue-specific
    rămân shim-uri de import, nu copii ale rezolvării simbolului.
    """
    if not symbol_env_keys:
        raise ValueError("bind_notify cere cel puțin o cheie de mediu")

    def bound_notify(
        title: str,
        body: str,
        source: str,
        price: Optional[float] = None,
        desktop: bool = False,
        symbol: Optional[str] = None,
        email: Optional[bool] = None,
    ) -> None:
        resolved = symbol or next(
            (os.environ[key] for key in symbol_env_keys if os.environ.get(key)),
            default_symbol,
        )
        notify(
            title, body, source, resolved,
            price=price, desktop=desktop, email=email,
        )

    bound_notify.__name__ = "notify"
    return bound_notify
