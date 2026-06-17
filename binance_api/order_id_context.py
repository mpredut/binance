import itertools
import os
import re
import sys
import threading
import time


CLIENT_ORDER_ID_MAX_LEN = 36

_PROCESS_NAME_OVERRIDES = {
    "tradeall": "TA",
    "trade": "T",
    "trade2": "T2",
    "trade3": "T3",
    "trade4": "T4",
    "trade5": "T5",
    "rtrade": "RT",
    "monitortrades": "MT",
    "monitororder": "MO",
    "market_alerts": "MA",
    "cache_watchdog": "CW",
    "server": "SRV",
}

_CLIENT_ORDER_COUNTER = itertools.count()
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _script_stem():
    if not sys.argv:
        return "python"
    script = sys.argv[0] or "python"
    stem, _ = os.path.splitext(os.path.basename(script))
    return stem or "python"


def _derive_process_name(stem):
    key = stem.lower()
    if key in _PROCESS_NAME_OVERRIDES:
        return _PROCESS_NAME_OVERRIDES[key]

    parts = [part for part in re.split(r"[_\-.]+", stem) if part]
    if len(parts) > 1:
        return "".join(part[0].upper() for part in parts if part)

    alpha = re.sub(r"[^A-Za-z0-9]", "", stem)
    if not alpha:
        return "PY"
    return alpha[:6].upper()


def _sanitize_name(value, default="unknown"):
    safe = _SAFE_NAME_RE.sub("_", str(value or "")).strip("_")
    return safe or default


def _base36(value):
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    value = int(value)
    if value == 0:
        return "0"

    chars = []
    while value:
        value, rem = divmod(value, 36)
        chars.append(alphabet[rem])
    return "".join(reversed(chars))


PROCESS_NAME = _sanitize_name(_derive_process_name(_script_stem()), default="PY")


def set_process_name(name):
    global PROCESS_NAME
    PROCESS_NAME = _sanitize_name(name, default="PY")
    return PROCESS_NAME


def get_process_name():
    return PROCESS_NAME


def get_thread_name():
    return _sanitize_name(threading.current_thread().name, default="MainThread")


def create_client_order_id():
    prefix = _sanitize_name(f"{get_process_name()}_{get_thread_name()}", default=get_process_name())
    counter = next(_CLIENT_ORDER_COUNTER) % (36 * 36)
    suffix = f"_{_base36(int(time.time() * 1000))[-8:]}{_base36(counter).zfill(2)}"
    max_prefix_len = CLIENT_ORDER_ID_MAX_LEN - len(suffix)

    if len(prefix) > max_prefix_len:
        prefix = prefix[:max_prefix_len].rstrip("_-") or get_process_name()

    return f"{prefix}{suffix}"
