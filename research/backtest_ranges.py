"""
backtest_ranges.py — parseaza rangurile de test scrise ca text SIMPLU,
DIRECT deasupra unui parametru din orice fisier de config (23 iul,
research/UNIFIED_BACKTEST_PLAN.md, decizie user: text simplu, NU YAML/JSON).

Format (o linie, deasupra parametrului, in ORICE fisier — .env, .conf tip
INI, monitortrades.conf):

    # BACKTEST: 5.0, 6.0, 7.0, 8.0, 9.0
    mt.gain = 7.0

Genereic pe format: NU stie nimic despre .env vs INI vs monitortrades.conf —
cauta doar linia de comentariu "# BACKTEST: ..." IMEDIAT deasupra unei linii
"cheie = valoare" / "cheie=valoare" (whitespace optional in jurul lui "="),
via un regex simplu pe cheie. Comentariul trebuie sa fie chiar pe linia
anterioara — orice alta linie intre ele (alt comentariu, o linie goala) il
anuleaza, ca sa nu se atribuie gresit unui parametru diferit.

Fisiere de tip INI (instruments.conf) au sectiuni `[NUME]` care REFOLOSESC
aceleasi chei (ex. mt.gain apare atat in [BINANCE_BTC] cat si in
[BINANCE_TAO]) — cheia intoarsa e prefixata cu sectiunea curenta
("BINANCE_BTC.mt.gain"), ca sa nu se confunde/suprascrie una pe alta. Fisiere
FARA sectiuni (.env, monitortrades.conf) intorc cheia neschimbata.

De ce NU YAML/JSON separat (decizie explicita user): rangul de test traieste
LANGA valoarea reala, in acelasi fisier pe care un om il citeste oricum —
nu intr-un sidecar separat care poate deriva tacut de valoarea reala.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

_SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')
_BACKTEST_RE = re.compile(r'^\s*#\s*BACKTEST:\s*(.+?)\s*$')
_KEY_RE = re.compile(r'^\s*([A-Za-z0-9_.]+)\s*=\s*(.+?)\s*$')


def scan_backtest_ranges(path: str) -> Dict[str, List[str]]:
    """Intoarce {cheie: [valori_ca_string]} pt fiecare linie "# BACKTEST: ..."
    gasita imediat deasupra unei linii cheie=valoare, in `path`. Chei din
    fisiere cu sectiuni [NUME] (instruments.conf) sunt prefixate cu sectiunea
    ("BINANCE_BTC.mt.gain"). [] daca fisierul lipseste sau n-are nicio
    adnotare de acest tip."""
    out: Dict[str, List[str]] = {}
    if not os.path.exists(path):
        return out
    section: Optional[str] = None
    pending: Optional[List[str]] = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            sm = _SECTION_RE.match(line)
            if sm:
                section = sm.group(1)
                pending = None
                continue
            m = _BACKTEST_RE.match(line)
            if m:
                pending = [v.strip() for v in m.group(1).split(",") if v.strip()]
                continue
            m2 = _KEY_RE.match(line)
            if m2 and pending is not None:
                key = m2.group(1)
                full_key = f"{section}.{key}" if section else key
                out[full_key] = pending
                pending = None
            else:
                # orice alta linie (goala, alt comentariu, alta cheie fara
                # adnotare) rupe legatura -- comentariul trebuie sa fie CHIAR
                # deasupra, nu "undeva mai sus".
                pending = None
    return out


def scan_all(paths: List[str]) -> Dict[str, Dict[str, List[str]]]:
    """{path: {cheie: [valori]}} pt o lista de fisiere de config."""
    return {p: scan_backtest_ranges(p) for p in paths}
