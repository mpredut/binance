#!/usr/bin/env python3
"""
botcore.py — nucleul COMUN al utilitarelor de bot (logging, .env, HTTP, timp).
Zero dependinte externe (doar stdlib).

Sursa UNICA pentru functiile care erau duplicate (si incepusera sa divida) in
kraken/common.py, hyperliquid/common.py, 212trading/ipo_common.py. Fiecare dintre
acelea re-exporta de aici (compat inapoi: `from common import log` ramane valid).

NU includem `now_str()` — DIVERGE intentionat intre boti (212 pune si timezone ET,
kraken/HL doar Bucuresti); ramane per-provider. Transportul HTTP este însă comun;
shim-urile venue-urilor doar re-exportă forma JSON/form de aici.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

HTTP_TIMEOUT = 25
BUCHAREST = timezone(timedelta(hours=3))   # EEST vara

_LOCKS: dict = {}   # tine fd-urile de lock deschise cat traieste procesul (nu le colecta GC)


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).astimezone(BUCHAREST):%H:%M:%S}] {msg}", flush=True)


def single_instance(name: str, lockdir: str = "/tmp") -> None:
    """Single-instance guard: prima instanta obtine lock-ul EXCLUSIV (flock) pe
    <lockdir>/binance_<name>.lock si il tine cat traieste procesul; a doua instanta NU-l
    obtine -> iese (exit 0). Previne dubla-lansare = dubla-tranzactionare. Ca flock-ul din
    flota_start.sh, dar per-proces Python -> protejeaza indiferent CUM e lansat botul
    (bots_start, healthcheck, systemd, manual)."""
    path = os.path.join(lockdir, f"binance_{name}.lock")
    fd = open(path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"[{name}] ruleaza deja (lock activ: {path}) — ies.", flush=True)
        sys.exit(0)
    fd.write(str(os.getpid())); fd.flush()
    _LOCKS[name] = fd   # pastreaza referinta -> lock-ul ramane pana moare procesul


def _dotenv_pairs(path: str) -> tuple[list[tuple[str, str]], bool]:
    """Parsează o singură dată sintaxa comună; bool-ul indică o citire reușită."""
    if not os.path.exists(path):
        return [], False
    try:
        with open(path, "r", encoding="utf-8") as f:
            pairs = []
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                # sterge comentariile inline pt valori neghilimelate (VALUE=x  # comment)
                if not (val.startswith('"') or val.startswith("'")):
                    val = val.split("#")[0].strip()
                val = val.strip('"').strip("'")
                pairs.append((key, val))
        return pairs, True
    except OSError as e:
        log(f"  ! nu pot citi {path}: {e}")
        return [], False


def load_dotenv(path: str = ".env") -> None:
    """Incarca KEY=VALUE dintr-un .env in os.environ (fara a suprascrie mediul real)."""
    pairs, loaded = _dotenv_pairs(path)
    if not loaded:
        return
    for key, value in pairs:
        if key and key not in os.environ:
            os.environ[key] = value
    log(f"  .env incarcat din {path}")


def parse_dotenv(path: str) -> dict:
    """Ca load_dotenv, dar RETURNEAZA un dict (nu atinge os.environ). Necesar cand rulam
    mai multe active in ACELASI proces: fiecare isi ia config-ul in dict separat."""
    pairs, _loaded = _dotenv_pairs(path)
    out: dict[str, str] = {}
    for key, value in pairs:
        out[key] = value
    return out


def float_env(key: str, env: dict | None = None) -> float | None:
    """Float din env (os.environ implicit, sau un dict dat), ignorand comentariile inline.
    Superset: `env` optional -> compatibil si cu apelurile vechi float_env(key)."""
    src = os.environ if env is None else env
    raw = (src.get(key, "") or "").split("#")[0].strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def http_request(
    method: str,
    url: str,
    headers: dict | None = None,
    payload: dict | None = None,
    *,
    form: dict | None = None,
) -> tuple[int, bytes]:
    """Transport HTTP stdlib comun, cu același contract ``(status, body)``.

    ``payload`` este serializat JSON, iar ``form`` ca
    ``application/x-www-form-urlencoded``. Cele două forme sunt mutual exclusive.
    Erorile HTTP păstrează status/body; erorile de transport sunt fail-closed ca
    ``(0, b"")``, exact ca helper-ele venue-urilor pe care le înlocuiește.
    """
    if payload is not None and form is not None:
        raise ValueError("payload și form sunt mutual exclusive")

    request_headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        request_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded"
        )

    verb = method.upper()
    req = urllib.request.Request(
        url, data=data, headers=request_headers, method=verb,
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        log(f"  ! eroare retea {verb}: {e}")
        return 0, b""


def http_get(url: str, headers: dict | None = None) -> tuple[int, bytes]:
    return http_request("GET", url, headers=headers)


def http_post_json(
    url: str, payload: dict, headers: dict | None = None,
) -> tuple[int, bytes]:
    return http_request("POST", url, headers=headers, payload=payload)


def http_post_form(
    url: str, data: dict, headers: dict | None = None,
) -> tuple[int, bytes]:
    return http_request("POST", url, headers=headers, form=data)


# ── Comparatii "aproape egal" (procentual, DETERMINIST) ──────────────────────
# Sursa unica pt flota + boti. Inlocuieste utils.are_close (care avea
# random.randint in bucla de toleranta -> acelasi input putea da True SAU False
# in banda [tol*1.01, tol*1.5] — inacceptabil pt decizii de trading).

def diff_percent(value1: float, value2: float) -> float:
    """Diferenta procentuala simetrica (raportata la media absoluta a valorilor)."""
    if value1 == 0 and value2 == 0:
        return 0.0
    return abs(value1 - value2) / ((abs(value1) + abs(value2)) / 2) * 100


def are_close(value1: float, value2: float, tolerance_percent: float = 1.0) -> bool:
    """True daca valorile difera cu cel mult tolerance_percent (determinist).

    Pt praguri de pret: are_close(pret, prag, 0.05) -> pretul la 0.05% de prag
    conteaza ca atins (nu mai ratam o intrare la 2-3 centi de prag)."""
    return diff_percent(value1, value2) <= tolerance_percent


def diff_equals_percent(value1: float, value2: float, target_percent: float,
                        tolerance_percent: float = 1.0) -> bool:
    """True daca DIFERENTA procentuala dintre valori este ≈ target_percent
    (banda pe ambele parti, determinist). Alta intrebare decat are_close:
    nu "sunt apropiate valorile?", ci "difera cu ~X%?" — ex: "a scazut cu ~10%?".
    Inlocuieste utils.are_difference_equal_with_aprox_proc (care avea random)."""
    return abs(diff_percent(value1, value2) - target_percent) <= tolerance_percent
