#!/usr/bin/env python3
"""
tradeall_monitor.py — monitor SEPARAT, observational, pentru modelul de
trend al tradeall. NU importa si NU porneste tradeall.py / bapi_placeorder.py
— citeste doar fisiere text (pipe-delimited) din logger/ si cache_instant_trend.json.

Mod LIVE (implicit), in bucla (implicit 5s):
  1. Esantioneaza pretul curent (cache_instant_trend.json) in propriul jurnal
     logger/tradeall_price_samples_YYYY-MM-DD.log (decuplat de tradeall).
  2. Citeste logger/tradeall_decisions_*.log (doar trend_start) si
     logger/order_outcomes_*.log (filtrat pe caller=tradeall.py).
  3. Reconstruieste regiunile de trend (start -> urmatorul start).
  4. Redeseneaza, per simbol, doua PNG-uri (zi / saptamana) cu linia de pret,
     fundal colorat dupa stare si markeri BUY/SELL (executat/refuzat).
  5. Scrie tradeall_live.html static (auto-refresh + toggle zi/saptamana).

Rulare manuala (LIVE):
    ./tradeall_monitor.py [--symbols BTCUSDC,TAOUSDC] [--interval 5]
    apoi deschide tradeall_live.html intr-un browser (local sau prin ngrok).

Mod BACKTEST (randeaza rezultatele unui run tradeall_backtest.py, A5):
    ./tradeall_monitor.py --backtest-dir logger/backtest/<run_id> --symbols BTCUSDC
    (fara esantionare live; citeste fisierele FLATE din acel folder, fereastra
    = tot intervalul reluat pana acum; scrie PNG-ul in acelasi folder)
"""
import argparse
import os
import time
import json
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

ROOT = os.path.dirname(os.path.abspath(__file__))
LOGGER_DIR = os.path.join(ROOT, "logger")
CACHE_TREND_PATH = os.path.join(ROOT, "cachedb", "cache_instant_trend.json")

# Folder DEDICAT pt output-ul modului LIVE (PNG-uri + HTML) — separat de restul
# repo-ului in mod deliberat: e singurul folder sigur de servit pe web (nu contine
# .env/chei/cachedb — doar grafice generate), spre deosebire de ROOT.
LIVE_OUT_DIR = os.path.join(ROOT, "tradeall_live")

PRICE_SAMPLES_PREFIX = "tradeall_price_samples_"
DECISIONS_PREFIX = "tradeall_decisions_"
OUTCOMES_PREFIX = "order_outcomes_"
SHADOW_PREFIX = "tradeall_shadow_"
SHADOW_COLOR = "#8250df"   # violet — semnalele shadow (Kalman), distinct de verde/rosu

STATE_COLORS = {"UP": "#1a7f37", "DOWN": "#cf222e", "HOLD": "#8c8c8c"}
DAY_SECONDS = 24 * 3600
WEEK_SECONDS = 7 * DAY_SECONDS


def _sanitize_field(value):
    return str(value).replace("|", "/").replace("\n", " ")


def _daily_log_path(prefix, date):
    return os.path.join(LOGGER_DIR, f"{prefix}{date.isoformat()}.log")


def _read_pipe_log(path, ncols):
    """Citeste un fisier pipe-delimited; randuri malformate sunt ignorate
    (robust la scrieri concurente/intrerupte)."""
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("|")
                if len(parts) != ncols:
                    continue
                rows.append(parts)
    except OSError:
        pass
    return rows


def _log_dates(days_back):
    today = datetime.now().date()
    return [today - timedelta(days=i) for i in range(days_back)]


# ── Pas B: esantionare proprie de pret (nu atinge tradeall) ───────────────────

def sample_current_prices(symbols):
    if not os.path.exists(CACHE_TREND_PATH):
        return
    try:
        with open(CACHE_TREND_PATH, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[tradeall_monitor] eroare citire cache_instant_trend.json: {e}")
        return

    os.makedirs(LOGGER_DIR, exist_ok=True)
    path = _daily_log_path(PRICE_SAMPLES_PREFIX, datetime.now().date())
    try:
        with open(path, "a", encoding="utf-8") as f:
            for symbol in symbols:
                entry = snapshot.get(symbol)
                if not entry or "current_price" not in entry:
                    continue
                # ts-ul SNAPSHOT-ului, nu ceasul nostru: daca sursa e inghetata
                # (incident 19 iul: cacheManager mort, pret vechi de 27 min),
                # esantionul cu timestamp CURENT dar pret VECHI s-ar intercala cu
                # sursele vii si ar desena un "bloc" zimtat fals pe grafic.
                f.write(f"{entry.get('ts', time.time())}|{_sanitize_field(symbol)}|{entry['current_price']}\n")
    except OSError as e:
        print(f"[tradeall_monitor] eroare scriere esantioane pret: {e}")


# ── Incarcare jurnale (Pas A / A2) ────────────────────────────────────────────

def load_price_samples(symbol, days_back):
    ts_list, px_list = [], []
    for d in reversed(_log_dates(days_back)):
        for ts, sym_, price in _read_pipe_log(_daily_log_path(PRICE_SAMPLES_PREFIX, d), 3):
            if sym_ != symbol:
                continue
            try:
                ts_list.append(float(ts))
                px_list.append(float(price))
            except ValueError:
                continue
    return ts_list, px_list


def _load_cachedb_price_entries(filename, symbol):
    """Citeste [[ts_ms, price], ...] dintr-un fisier cache24-format din cachedb/."""
    path = os.path.join(ROOT, "cachedb", filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("items", {}).get(symbol, [])
    except (OSError, json.JSONDecodeError, ValueError):
        return []   # fisierul poate fi in curs de scriere de alt proces — sarim ciclul asta


def _load_history_jsonl_tail(symbol, max_bytes=4 * 1024 * 1024):
    """Coada istoricului lung (cachedb/cache_price_{s}.jsonl, ~11 luni, rar) —
    citim doar ultimii max_bytes (fisierul are ~40MB; coada acopera saptamani),
    suficient pentru fereastra de 7 zile fara sa scanam tot fisierul la fiecare ciclu."""
    path = os.path.join(ROOT, "cachedb", f"cache_price_{symbol}.jsonl")
    if not os.path.exists(path):
        return []
    entries = []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.split("\n")[1:]   # prima linie e probabil taiata de seek — o sarim
        for line in lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("s") == symbol:
                    entries.append(rec["i"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    except OSError:
        pass
    return entries


def load_price_series_live(symbol, days_back, include_history=True):
    """Linia de pret pentru modul LIVE, din TOATE sursele deja existente,
    imbinata si sortata — graficul e plin din prima clipa, nu asteapta ca
    monitorul sa-si acumuleze propriile esantioane:
      1. cachedb/cache_24price_long_{s}.json — arhivatorul (dens, retentie lunga)
      2. cachedb/cache_24price_{s}.json      — cache-ul live al flotei (dens, 24h)
      3. cachedb/cache_price_{s}.jsonl       — istoricul lung (rar ~7min/tick, doar coada)
      4. logger/tradeall_price_samples_*.log — esantioanele proprii (fallback/umplere)
    Punctele dense au prioritate la coliziune de timestamp.
    include_history=False: sare peste istoricul lung (3) — ferestrele <=24h il
    au oricum acoperit de cache-urile dense; economiseste ~4MB de parsare/ciclu.
    """
    # Imbinare pe GALETI de 1s: sursele au offset-uri de ceas diferite la ~1s;
    # fara aliniere, doua fluxuri cu preturi usor diferite se intercaleaza si
    # deseneaza un "bloc" zimtat fals. O galeata = un punct; prioritatea o dau
    # sursele dense (scrise ULTIMELE suprascriu galeata).
    points = {}
    own_ts, own_px = load_price_samples(symbol, days_back)
    for t, p in zip(own_ts, own_px):
        points[int(t)] = p
    if include_history:
        for entry in _load_history_jsonl_tail(symbol):
            try:
                points[int(entry[0] / 1000.0)] = float(entry[1])
            except (TypeError, ValueError, IndexError):
                continue
    for fname in (f"cache_24price_{symbol}.json", f"cache_24price_long_{symbol}.json"):
        for entry in _load_cachedb_price_entries(fname, symbol):
            try:
                points[int(entry[0] / 1000.0)] = float(entry[1])
            except (TypeError, ValueError, IndexError):
                continue
    ts_sorted = sorted(points)
    return ts_sorted, [points[t] for t in ts_sorted]


def load_trend_starts(symbol, days_back):
    events = []
    for d in reversed(_log_dates(days_back)):
        for row in _read_pipe_log(_daily_log_path(DECISIONS_PREFIX, d), 7):
            ts, sym_, event, state, old_state, price, prev_confirm = row
            if sym_ != symbol or event != "trend_start":
                continue
            try:
                events.append({"ts": float(ts), "state": state, "old_state": old_state,
                                "price": float(price), "prev_confirm_count": prev_confirm})
            except ValueError:
                continue
    events.sort(key=lambda e: e["ts"])
    return events


def load_order_events(symbol, days_back):
    """Doar evenimentele generate de tradeall.py (order_outcomes e fleet-wide)."""
    events = []
    for d in reversed(_log_dates(days_back)):
        for row in _read_pipe_log(_daily_log_path(OUTCOMES_PREFIX, d), 9):
            ts, sym_, side, price, qty, outcome, refuse_reason, caller, motivation = row
            if sym_ != symbol or caller != "tradeall.py":
                continue
            try:
                events.append({
                    "ts": float(ts), "side": side, "price": float(price),
                    "outcome": outcome, "reason": motivation or refuse_reason,
                })
            except ValueError:
                continue
    events.sort(key=lambda e: e["ts"])
    return events


def _parse_shadow_rows(rows, symbol):
    """Randuri shadow: ts|symbol|signal|event|state|old_state|price|vel|vel_std."""
    events = []
    for row in rows:
        ts, sym_, signal, event, state, old_state, price, vel, vel_std = row
        if sym_ != symbol or event != "trend_start":
            continue
        try:
            events.append({"ts": float(ts), "signal": signal, "state": int(state),
                            "old_state": int(old_state), "price": float(price),
                            "vel": vel, "vel_std": vel_std})
        except ValueError:
            continue
    events.sort(key=lambda e: e["ts"])
    return events


def load_shadow_events(symbol, days_back):
    rows = []
    for d in reversed(_log_dates(days_back)):
        rows.extend(_read_pipe_log(_daily_log_path(SHADOW_PREFIX, d), 9))
    return _parse_shadow_rows(rows, symbol)


def load_backtest_shadow_events(directory, symbol):
    return _parse_shadow_rows(_read_pipe_log(os.path.join(directory, "tradeall_shadow.log"), 9), symbol)


def build_trend_regions(events, window_start, window_end):
    """Regiune de stare: de la un trend_start pana la urmatorul (Pas A/C, varianta b din plan)."""
    regions = []
    for i, ev in enumerate(events):
        end = events[i + 1]["ts"] if i + 1 < len(events) else window_end
        start, end = max(ev["ts"], window_start), min(end, window_end)
        if end > start:
            regions.append((start, end, ev["state"]))
    return regions


# ── Incarcare jurnale BACKTEST (fisiere flate, fara rotire zilnica) ───────────

def load_backtest_price_samples(directory, symbol):
    ts_list, px_list = [], []
    for ts, sym_, price in _read_pipe_log(os.path.join(directory, "tradeall_price_samples.log"), 3):
        if sym_ != symbol:
            continue
        try:
            ts_list.append(float(ts))
            px_list.append(float(price))
        except ValueError:
            continue
    return ts_list, px_list


def load_backtest_trend_starts(directory, symbol):
    events = []
    for row in _read_pipe_log(os.path.join(directory, "tradeall_decisions.log"), 7):
        ts, sym_, event, state, old_state, price, prev_confirm = row
        if sym_ != symbol or event != "trend_start":
            continue
        try:
            events.append({"ts": float(ts), "state": state, "old_state": old_state,
                            "price": float(price), "prev_confirm_count": prev_confirm})
        except ValueError:
            continue
    events.sort(key=lambda e: e["ts"])
    return events


def load_backtest_order_events(directory, symbol):
    """Backtest = un singur run, un singur caller ('backtest') — nu mai filtram dupa caller."""
    events = []
    for row in _read_pipe_log(os.path.join(directory, "order_outcomes.log"), 9):
        ts, sym_, side, price, qty, outcome, refuse_reason, caller, motivation = row
        if sym_ != symbol:
            continue
        try:
            events.append({
                "ts": float(ts), "side": side, "price": float(price),
                "outcome": outcome, "reason": motivation or refuse_reason,
            })
        except ValueError:
            continue
    events.sort(key=lambda e: e["ts"])
    return events


# ── Randare (Pas C) ────────────────────────────────────────────────────────

def render_chart(symbol, window_label, window_start, window_end,
                  price_ts, price_vals, trend_events, order_events, out_path,
                  state_text=None, shadow_events=None):
    """Deseneaza un grafic dintr-un set de date DEJA incarcat (live sau backtest).
    state_text (optional): caseta cu starea CURENTA a analizei (dreapta-sus).
    shadow_events (optional): tranzitiile semnalelor SHADOW (Kalman) — romburi
    violet + linii punctate, de comparat vizual cu modelul actual (verde/rosu)."""
    vis_ts, vis_px = [], []
    for t, p in zip(price_ts, price_vals):
        if window_start <= t <= window_end:
            vis_ts.append(t)
            vis_px.append(p)

    fig, ax = plt.subplots(figsize=(11, 5))

    for start, end, state in build_trend_regions(trend_events, window_start, window_end):
        ax.axvspan(datetime.fromtimestamp(start), datetime.fromtimestamp(end),
                   color=STATE_COLORS.get(state, "#dddddd"), alpha=0.15, lw=0)

    # Marcaj PUNCTUAL la fiecare trend_start (nu doar zona de fundal) — momentul exact
    # in care starea s-a schimbat, cerut explicit: "nu doar la general". Etichetele UP
    # merg DEASUPRA liniei de pret, cele DOWN DEDESUBT. Cand mai multe evenimente de
    # ACEEASI directie cad apropiat in timp (cluster), le esalonam progresiv (fiecare
    # tot mai departe de linia de pret) ca sa nu se suprapuna intre ele.
    visible_trend_starts = [e for e in trend_events if window_start <= e["ts"] <= window_end]
    cluster_gap = max((window_end - window_start) * 0.02, 1.0)   # 2% din fereastra vizibila
    last_ts_by_state = {}
    stagger_by_state = {}
    for e in visible_trend_starts:
        state = e["state"]
        prev_ts = last_ts_by_state.get(state)
        stagger_by_state[state] = stagger_by_state.get(state, -1) + 1 \
            if prev_ts is not None and (e["ts"] - prev_ts) < cluster_gap else 0
        last_ts_by_state[state] = e["ts"]
        level = stagger_by_state[state]

        color = STATE_COLORS.get(state, "#888888")
        t = datetime.fromtimestamp(e["ts"])
        ax.axvline(t, color=color, ls="--", lw=1, alpha=0.6, zorder=3)
        ax.scatter(t, e["price"], marker="o", color=color, s=40, zorder=6,
                   edgecolors="white", linewidths=0.8)
        label = f"{e['old_state']}→{e['state']}"
        if e.get("prev_confirm_count"):
            label += f" ({e['prev_confirm_count']})"
        step = 15 * level
        y_offset = 16 + step if state == "UP" else -20 - step
        va = "bottom" if state == "UP" else "top"
        ax.annotate(label, (t, e["price"]), fontsize=5.5, xytext=(3, y_offset),
                    textcoords="offset points", color=color, va=va, rotation=0)

    if vis_ts:
        # Afisare subesantionata cand sunt prea multe puncte (ziua = ~86k la 1s):
        # peste ~6k puncte pe ~1100px linia devine o banda compacta ilizibila.
        if len(vis_ts) > 6000:
            stride = len(vis_ts) // 6000 + 1
            vis_ts = vis_ts[::stride]
            vis_px = vis_px[::stride]
        ax.plot([datetime.fromtimestamp(t) for t in vis_ts], vis_px,
                color="#1f6feb", lw=0.8, label="pret")
    else:
        ax.text(0.5, 0.5, "Inca nu sunt esantioane de pret\n(lasa monitorul sa ruleze putin)",
                ha="center", va="center", transform=ax.transAxes, color="#888888")

    # SHADOW (Kalman): romburi violet la tranzitii, cu directia noua ca eticheta.
    # Linie punctata (nu dashed) — vizual distinct de trend_start-urile modelului.
    shadow_map = {1: "K:UP", -1: "K:DOWN", 0: "K:FLAT"}
    for e in (shadow_events or []):
        if not (window_start <= e["ts"] <= window_end):
            continue
        t = datetime.fromtimestamp(e["ts"])
        ax.axvline(t, color=SHADOW_COLOR, ls=":", lw=1, alpha=0.7, zorder=3)
        ax.scatter(t, e["price"], marker="D", color=SHADOW_COLOR, s=32, zorder=6,
                   edgecolors="white", linewidths=0.6)
        ax.annotate(shadow_map.get(e["state"], "?"), (t, e["price"]), fontsize=5.5,
                    xytext=(3, 8), textcoords="offset points", color=SHADOW_COLOR)

    visible_orders = [e for e in order_events if window_start <= e["ts"] <= window_end]
    executed = [e for e in visible_orders if e["outcome"] == "executed"]
    refused = [e for e in visible_orders if e["outcome"] == "refused"]

    # Etichete text per-marker DOAR cand sunt putine — cu sute de refuzuri (ex. tradeall
    # incearca BUY la fiecare tick si garda refuza), textul ar acoperi tot graficul.
    # Markerii raman toti; caseta-sumar de mai jos tine oricum contorizarea completa.
    MAX_ANNOTATED = 25

    for e in executed:
        marker = "^" if e["side"] == "BUY" else "v"
        color = STATE_COLORS["UP"] if e["side"] == "BUY" else STATE_COLORS["DOWN"]
        ax.scatter(datetime.fromtimestamp(e["ts"]), e["price"], marker=marker,
                   color=color, s=90, zorder=5)
        if len(executed) <= MAX_ANNOTATED:
            ax.annotate(e["reason"], (datetime.fromtimestamp(e["ts"]), e["price"]),
                        fontsize=7, xytext=(4, 6), textcoords="offset points", color=color)

    for e in refused:
        marker = "^" if e["side"] == "BUY" else "v"
        color = STATE_COLORS["UP"] if e["side"] == "BUY" else STATE_COLORS["DOWN"]
        ax.scatter(datetime.fromtimestamp(e["ts"]), e["price"], marker=marker,
                   facecolors="none", edgecolors=color, s=90, zorder=5, linewidths=1.5)
        if len(refused) <= MAX_ANNOTATED:
            ax.annotate(e["reason"], (datetime.fromtimestamp(e["ts"]), e["price"]),
                        fontsize=7, xytext=(4, -10), textcoords="offset points", color=color)

    summary = (
        f"BUY  executat: {sum(1 for e in executed if e['side'] == 'BUY')}   "
        f"refuzat: {sum(1 for e in refused if e['side'] == 'BUY')}\n"
        f"SELL executat: {sum(1 for e in executed if e['side'] == 'SELL')}   "
        f"refuzat: {sum(1 for e in refused if e['side'] == 'SELL')}"
    )
    ax.text(0.01, 0.98, summary, transform=ax.transAxes, va="top", fontsize=9,
            family="monospace", bbox=dict(boxstyle="round", fc="white", ec="#cccccc", alpha=0.9))

    # Axa X = fereastra CERUTA, nu doar intervalul cu date — altfel "ziua" si
    # "saptamana" arata identic cand toate datele disponibile incap in 24h.
    if state_text:
        ax.text(0.99, 0.98, state_text, transform=ax.transAxes, va="top", ha="right",
                fontsize=8, family="monospace",
                bbox=dict(boxstyle="round", fc="#fffbe6", ec="#d4a017", alpha=0.95))

    ax.set_xlim(datetime.fromtimestamp(window_start), datetime.fromtimestamp(window_end))
    ax.set_title(f"{symbol} — {window_label}  (actualizat {datetime.now().strftime('%H:%M:%S')})")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def format_state_text(entry, header):
    trend_map = {1: "UP", -1: "DOWN", 0: "FLAT"}
    text = (
        f"{header}\n"
        f"pret:      {entry.get('current_price', '?')}\n"
        f"trend:     {trend_map.get(entry.get('final_trend'), '?')}\n"
        f"grad rec:  {entry.get('gradient_recent', 0):+.4f}\n"
        f"slope mic: {entry.get('slope_small', 0):+.3f}\n"
        f"slope mare:{entry.get('slope_big', 0):+.3f}\n"
        f"epsilon:   {entry.get('epsilon', 0):.4f}"
    )
    # Randuri SHADOW — apar doar daca cheile exista in snapshot (tolerant)
    if entry.get("kalman_trend") is not None:
        text += (f"\nkalman:    {trend_map.get(entry.get('kalman_trend'), '?')} "
                 f"(v={entry.get('kalman_vel', 0):+.3f}%/min ±{entry.get('kalman_vel_std', 0):.3f})")
    v1h = entry.get("vol_1h_pct")
    if v1h is not None:
        text += (f"\nvol 1h:    {v1h:.2f}% → re:{entry.get('adapt_reentry_pct', '?')}% "
                 f"dca:{entry.get('adapt_dca_pct', '?')}%")
    return text


def build_analysis_state_text(symbol):
    """Starea CURENTA a analizei tradeall LIVE (cache_instant_trend.json),
    combinata cu starea SHADOW (cachedb/shadow_state.json — scrisa direct de
    tradeall, pentru ca fisierul de trend e detinut de procesul cacheManager)."""
    try:
        with open(CACHE_TREND_PATH, "r", encoding="utf-8") as f:
            entry = json.load(f).get(symbol)
    except (OSError, json.JSONDecodeError):
        entry = None
    if not entry:
        return None
    try:
        with open(os.path.join(ROOT, "cachedb", "shadow_state.json"), "r", encoding="utf-8") as f:
            sh = json.load(f).get(symbol)
        if sh:
            entry = {**entry, **{k: v for k, v in sh.items() if k not in ("ts", "price")}}
    except (OSError, json.JSONDecodeError):
        pass
    age = time.time() - entry.get("ts", 0)
    return format_state_text(entry, f"ANALIZA ACUM ({age:.0f}s in urma)")


def build_backtest_state_text(directory, symbol):
    """Starea analizei din SIMULARE (analysis_state.json scris de tradeall_backtest.py)."""
    path = os.path.join(directory, "analysis_state.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f).get(symbol)
    except (OSError, json.JSONDecodeError):
        entry = None
    if not entry:
        return None
    sim_time = datetime.fromtimestamp(entry.get("ts", 0)).strftime("%m-%d %H:%M:%S")
    return format_state_text(entry, f"ANALIZA SIMULATA ({sim_time})")


def render_state_image(state_text, out_path):
    """Caseta de stare ca imagine SEPARATA (aratata pe grafic doar la hover, din HTML)."""
    fig = plt.figure(figsize=(4.6, 2.5))
    fig.patch.set_alpha(0.0)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.text(0.04, 0.96, state_text, va="top", ha="left", fontsize=11, family="monospace",
            bbox=dict(boxstyle="round,pad=0.6", fc="#fffbe6", ec="#d4a017", alpha=0.97))
    fig.savefig(out_path, dpi=110, transparent=True)
    plt.close(fig)


def render_symbol_chart_live(symbol, window_label, window_seconds, out_path):
    window_end = time.time()
    window_start = window_end - window_seconds
    days_back = 9 if window_seconds > DAY_SECONDS else 2  # marja pt regiuni incepute inainte de fereastra
    include_history = window_seconds > DAY_SECONDS   # istoricul lung doar pt saptamana
    render_chart(symbol, window_label, window_start, window_end,
                 *load_price_series_live(symbol, days_back, include_history=include_history),
                 load_trend_starts(symbol, days_back),
                 load_order_events(symbol, days_back), out_path,
                 shadow_events=load_shadow_events(symbol, days_back))


def render_symbol_chart_backtest(symbol, directory, out_path, window_hours=None):
    """Daca window_hours e None: fereastra = tot intervalul reluat pana acum.
    Daca window_hours e dat: fereastra GLISANTA de acea lungime, ancorata la cel
    mai recent timestamp SIMULAT scris pana acum — apelata repetat (bucla din
    main(), la fiecare --interval secunde) in timp ce tradeall_backtest.py ruleaza
    concurent, fereastra "aluneca" urmarind ceasul simulat, ca un proces dinamic:
    evenimentele apar pe cadru exact cand backtester-ul ajunge la ele."""
    price_ts, price_vals = load_backtest_price_samples(directory, symbol)
    trend_events = load_backtest_trend_starts(directory, symbol)
    order_events = load_backtest_order_events(directory, symbol)

    all_ts = price_ts + [e["ts"] for e in trend_events] + [e["ts"] for e in order_events]
    if not all_ts:
        window_start, window_end = time.time() - DAY_SECONDS, time.time()
        label = "backtest (tot intervalul reluat)"
    elif window_hours:
        window_end = max(all_ts)
        window_start = window_end - window_hours * 3600
        label = (f"fereastra glisanta {window_hours}h (ceas simulat: "
                 f"{datetime.fromtimestamp(window_end).strftime('%Y-%m-%d %H:%M')})")
    else:
        window_start, window_end = min(all_ts), max(all_ts)
        label = "backtest (tot intervalul reluat)"

    render_chart(symbol, label, window_start, window_end,
                 price_ts, price_vals, trend_events, order_events, out_path,
                 shadow_events=load_backtest_shadow_events(directory, symbol))


def render_backtest_chunks(symbol, directory, chunk_hours=24, out_dir=None):
    """Genereaza o SERIE de cadre (nu un singur grafic dens) — cate o imagine per
    bucata de chunk_hours ore, ca sa poti 'plimba o fereastra de detaliu' peste tot
    intervalul reluat. Fiecare cadru are mult mai putine evenimente -> etichetele nu
    se mai calca intre ele, si se vede mai mult detaliu per bucata."""
    out_dir = out_dir or directory
    price_ts, price_vals = load_backtest_price_samples(directory, symbol)
    trend_events = load_backtest_trend_starts(directory, symbol)
    order_events = load_backtest_order_events(directory, symbol)

    all_ts = price_ts + [e["ts"] for e in trend_events] + [e["ts"] for e in order_events]
    if not all_ts:
        print(f"[tradeall_monitor] {symbol}: inca nu sunt date pentru cadre")
        return []

    chunk_sec = chunk_hours * 3600
    range_start, range_end = min(all_ts), max(all_ts)
    n_chunks = int((range_end - range_start) // chunk_sec) + 1

    paths = []
    for i in range(n_chunks):
        c_start = range_start + i * chunk_sec
        c_end = min(c_start + chunk_sec, range_end)
        label = f"{datetime.fromtimestamp(c_start).strftime('%Y-%m-%d %H:%M')} " \
                f"→ {datetime.fromtimestamp(c_end).strftime('%Y-%m-%d %H:%M')}  (cadru {i+1}/{n_chunks})"
        fname = f"tradeall_live_{symbol}_frame{i+1:03d}_{datetime.fromtimestamp(c_start).strftime('%Y%m%d_%H%M')}.png"
        path = os.path.join(out_dir, fname)
        render_chart(symbol, label, c_start, c_end, price_ts, price_vals,
                     trend_events, order_events, path)
        paths.append(path)
    return paths


# ── HTML static cu toggle zi/saptamana + auto-refresh (fara server) ──────────

def write_html(symbols, live_minutes=60):
    blocks = []
    for s in symbols:
        blocks.append(f'''
  <div class="chart">
    <h3>{s}</h3>
    <div class="chart-wrap">
      <img class="view-live" src="tradeall_live_{s}_live.png" alt="{s} live">
      <img class="view-day" src="tradeall_live_{s}_ziua.png" alt="{s} zi" style="display:none">
      <img class="view-week" src="tradeall_live_{s}_saptamana.png" alt="{s} saptamana" style="display:none">
      <img class="state-overlay" src="tradeall_live_{s}_state.png" alt="{s} stare">
    </div>
  </div>''')

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>tradeall — live</title>
<style>
body {{ font-family: -apple-system, sans-serif; background:#111; color:#eee; margin: 16px; }}
img {{ max-width: 100%; border: 1px solid #333; border-radius: 4px; }}
button {{ margin: 4px 6px 14px 0; padding: 6px 16px; border-radius: 4px; border: 1px solid #444;
         background:#222; color:#eee; cursor:pointer; }}
button.active {{ background:#1f6feb; border-color:#1f6feb; }}
.chart {{ margin-bottom: 24px; }}
.chart-wrap {{ position: relative; display: inline-block; max-width: 100%; }}
.state-overlay {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
                  opacity: 0; transition: opacity .15s; pointer-events: none;
                  max-width: 45%; border: none; }}
.chart-wrap:hover .state-overlay {{ opacity: 1; }}
</style></head>
<body>
<h2>tradeall — monitor live</h2>
<button id="btn-live" class="active" onclick="show('live')">Live ({live_minutes:.0f} min)</button>
<button id="btn-day" onclick="show('day')">Zi</button>
<button id="btn-week" onclick="show('week')">Saptamana</button>
{"".join(blocks)}
<script>
function bust() {{
  document.querySelectorAll('img').forEach(function(img) {{
    var base = img.src.split('?')[0];
    img.src = base + '?t=' + Date.now();
  }});
}}
function show(which) {{
  ['live', 'day', 'week'].forEach(function(v) {{
    document.querySelectorAll('.view-' + v).forEach(function(i) {{ i.style.display = which === v ? '' : 'none'; }});
    document.getElementById('btn-' + v).classList.toggle('active', which === v);
  }});
}}
setInterval(bust, 2500);
</script>
</body></html>"""
    os.makedirs(LIVE_OUT_DIR, exist_ok=True)
    with open(os.path.join(LIVE_OUT_DIR, "tradeall_live.html"), "w", encoding="utf-8") as f:
        f.write(html)


def write_backtest_html(directory, symbols):
    """HTML minimal pt un run de backtest: fara toggle zi/saptamana (interval custom)."""
    os.makedirs(directory, exist_ok=True)   # backtester-ul poate sa nu fi creat inca folderul
    blocks = "".join(
        f'<div class="chart"><h3>{s}</h3><div class="chart-wrap">'
        f'<img src="tradeall_live_{s}.png" alt="{s}">'
        f'<img class="state-overlay" src="tradeall_live_{s}_state.png" alt="{s} stare">'
        f'</div></div>' for s in symbols)
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>tradeall — backtest</title>
<style>
body {{ font-family: -apple-system, sans-serif; background:#111; color:#eee; margin: 16px; }}
img {{ max-width: 100%; border: 1px solid #333; border-radius: 4px; }}
.chart {{ margin-bottom: 24px; }}
.chart-wrap {{ position: relative; display: inline-block; max-width: 100%; }}
.state-overlay {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
                  opacity: 0; transition: opacity .15s; pointer-events: none;
                  max-width: 45%; border: none; }}
.chart-wrap:hover .state-overlay {{ opacity: 1; }}
</style></head>
<body>
<h2>tradeall — backtest ({os.path.basename(directory)})</h2>
{blocks}
<script>
setInterval(function() {{
  document.querySelectorAll('img').forEach(function(img) {{
    img.src = img.src.split('?')[0] + '?t=' + Date.now();
  }});
}}, 5000);
</script>
</body></html>"""
    with open(os.path.join(directory, "tradeall_live_backtest.html"), "w", encoding="utf-8") as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="BTCUSDC,TAOUSDC",
                         help="listă separată prin virgulă (implicit: BTCUSDC,TAOUSDC)")
    parser.add_argument("--interval", type=float, default=2.0,
                         help="secunde intre cicluri (live + stare analiza; implicit 2)")
    parser.add_argument("--live-minutes", type=float, default=60.0,
                         help="fereastra tabului Live, in minute (implicit 60; TAO se misca rar "
                              "— la 30min fereastra arata mai mult gol decat semnal)")
    parser.add_argument("--day-refresh", type=float, default=30.0,
                         help="la cate secunde se redeseneaza graficul pe ZI (implicit 30)")
    parser.add_argument("--week-refresh", type=float, default=300.0,
                         help="la cate secunde se redeseneaza graficul pe SAPTAMANA (implicit 300)")
    parser.add_argument("--backtest-dir", default=None,
                         help="daca e dat: mod BACKTEST — randeaza folderul unui run "
                              "tradeall_backtest.py in loc sa ruleze live")
    parser.add_argument("--frame-hours", type=float, default=None,
                         help="cu --backtest-dir: in loc de UN grafic dens cu tot intervalul, "
                              "genereaza o SERIE de cadre STATICE (imagini), cate unul per N ore. "
                              "Genereaza o data si iese (nu ruleaza in bucla).")
    parser.add_argument("--window-hours", type=float, default=None,
                         help="cu --backtest-dir (fara --frame-hours): fereastra GLISANTA de N ore, "
                              "ancorata la ceasul simulat curent — ruleaza in bucla (ca live), "
                              "aluneca pe masura ce tradeall_backtest.py scrie date noi in paralel; "
                              "evenimentele apar pe cadru exact cand backtester-ul ajunge la ele")
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.backtest_dir and args.frame_hours:
        directory = os.path.abspath(args.backtest_dir)
        for symbol in symbols:
            paths = render_backtest_chunks(symbol, directory, chunk_hours=args.frame_hours)
            print(f"[tradeall_monitor] {symbol}: {len(paths)} cadre generate in {directory}")
            for p in paths:
                print(f"    {p}")
        return

    if args.backtest_dir:
        directory = os.path.abspath(args.backtest_dir)
        write_backtest_html(directory, symbols)
        html_path = os.path.join(directory, "tradeall_live_backtest.html")
        mode = f"fereastra glisanta {args.window_hours}h" if args.window_hours else "tot intervalul"
        print(f"[tradeall_monitor] BACKTEST ({mode}): {directory} | simboluri: {symbols} | "
              f"randare la {args.interval}s")
        print(f"[tradeall_monitor] deschide in browser: {html_path}")
        try:
            while True:
                for symbol in symbols:
                    try:
                        render_symbol_chart_backtest(
                            symbol, directory, os.path.join(directory, f"tradeall_live_{symbol}.png"),
                            window_hours=args.window_hours)
                        state_text = build_backtest_state_text(directory, symbol)
                        if state_text:
                            render_state_image(state_text,
                                                os.path.join(directory, f"tradeall_live_{symbol}_state.png"))
                    except Exception as e:
                        print(f"[tradeall_monitor] eroare randare {symbol}: {e}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[tradeall_monitor] oprit.")
        return

    write_html(symbols, live_minutes=args.live_minutes)
    html_path = os.path.join(LIVE_OUT_DIR, "tradeall_live.html")
    print(f"[tradeall_monitor] simboluri: {symbols} | randare la {args.interval}s")
    print(f"[tradeall_monitor] deschide in browser: {html_path}")

    # ESALONARE: live + starea analizei la FIECARE ciclu (senzatie de real-time);
    # ziua/saptamana mai rar — la scara lor, nimic vizibil nu se schimba in 3s,
    # iar randarea lor era ce facea ciclul sa dureze ~17s in loc de ~3s.
    last_day = last_week = 0.0
    try:
        while True:
            cycle_start = time.time()
            sample_current_prices(symbols)
            for symbol in symbols:
                try:
                    render_symbol_chart_live(symbol, f"LIVE ultimele {args.live_minutes:.0f} min",
                                              args.live_minutes * 60,
                                              os.path.join(LIVE_OUT_DIR, f"tradeall_live_{symbol}_live.png"))
                    state_text = build_analysis_state_text(symbol)
                    if state_text:
                        render_state_image(state_text,
                                            os.path.join(LIVE_OUT_DIR, f"tradeall_live_{symbol}_state.png"))
                    if cycle_start - last_day >= args.day_refresh:
                        render_symbol_chart_live(symbol, "ultimele 24h", DAY_SECONDS,
                                                  os.path.join(LIVE_OUT_DIR, f"tradeall_live_{symbol}_ziua.png"))
                    if cycle_start - last_week >= args.week_refresh:
                        render_symbol_chart_live(symbol, "ultimele 7 zile", WEEK_SECONDS,
                                                  os.path.join(LIVE_OUT_DIR, f"tradeall_live_{symbol}_saptamana.png"))
                except Exception as e:
                    print(f"[tradeall_monitor] eroare randare {symbol}: {e}")
            if cycle_start - last_day >= args.day_refresh:
                last_day = cycle_start
            if cycle_start - last_week >= args.week_refresh:
                last_week = cycle_start
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[tradeall_monitor] oprit.")


if __name__ == "__main__":
    main()
