#!/usr/bin/env python3
"""
Migrare cache_price_<SYM>.json (vechi, full-rewrite) → cache_price_<SYM>.jsonl (append).

La refactor s-a trecut de la .json la .jsonl + .meta, dar datele vechi din .json
nu au fost migrate. Acest script:
  • citeste punctele din .json vechi si din .jsonl existent (daca exista),
  • le imbina per simbol, deduplica pe timestamp, sorteaza crescator,
  • rescrie .jsonl atomic + regenereaza .meta,
  • redenumeste .json vechi → .json.bak (poti sterge .bak dupa verificare).

RULEAZA DOAR cu procesele writer (priceAnalysis etc.) OPRITE, ca sa nu existe
scriere concurenta pe .jsonl. Idempotent (re-rularea nu duplica).
"""
import os
import sys
import json
import glob
import time

# offline/legacy_tools/ este la doua niveluri sub radacina repo-ului.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Fisierele de cache stau in <repo>/cachedb/ (suprascris de BINANCE_CACHE_DIR sau --dir).
CACHE_DIR = os.environ.get("BINANCE_CACHE_DIR", os.path.join(REPO_ROOT, "cachedb"))


def _atomic_write_lines(path, lines):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for ln in lines:
            f.write(ln + "\n")
    os.replace(tmp, path)


def _entry_ts(item):
    # item e [ts, price] (lista) — folosim primul element ca timestamp
    if isinstance(item, (list, tuple)) and item:
        return item[0]
    if isinstance(item, dict):
        return item.get("time") or item.get("timestamp") or 0
    return 0


def _load_old_json(path):
    """{'items': {SYM: [[ts,price],...]}, 'fetchtime': {...}} → {SYM: [items]}"""
    with open(path) as f:
        data = json.load(f)
    items = data.get("items", data) if isinstance(data, dict) else {}
    return items if isinstance(items, dict) else {}


def _load_jsonl(path):
    """{SYM: [items]} din liniile {'s': SYM, 'i': item}."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out.setdefault(rec["s"], []).append(rec["i"])
            except Exception:
                continue
    return out


def migrate_file(json_path, dry_run=False):
    sym_from_name = os.path.basename(json_path)[len("cache_price_"):-len(".json")]
    jsonl_path = json_path[:-len(".json")] + ".jsonl"

    old = _load_old_json(json_path)
    new = _load_jsonl(jsonl_path)

    symbols = set(old) | set(new)
    merged = {}
    added_total = 0
    for sym in symbols:
        by_ts = {}
        for item in new.get(sym, []):          # intai noile (au prioritate la duplicat)
            by_ts[_entry_ts(item)] = item
        before = len(by_ts)
        for item in old.get(sym, []):
            by_ts.setdefault(_entry_ts(item), item)
        added = len(by_ts) - before
        added_total += added
        merged[sym] = [by_ts[k] for k in sorted(by_ts)]

    # scriere
    lines, max_ts, counts, fetchtime = [], 0, {}, {}
    for sym, items in merged.items():
        counts[sym] = len(items)
        for item in items:
            lines.append(json.dumps({"s": sym, "i": item}))
            max_ts = max(max_ts, _entry_ts(item))
        if items:
            fetchtime[sym] = _entry_ts(items[-1])

    print(f"[{sym_from_name}] old={sum(len(v) for v in old.values())} "
          f"jsonl={sum(len(v) for v in new.values())} → merged={sum(counts.values())} "
          f"(+{added_total} din vechi)")

    if dry_run:
        return added_total

    _atomic_write_lines(jsonl_path, lines)
    with open(jsonl_path + ".meta.tmp", "w") as mf:
        json.dump({"max_ts": max_ts, "saved_at": int(time.time() * 1000),
                   "fetchtime": fetchtime, "counts": counts}, mf)
    os.replace(jsonl_path + ".meta.tmp", jsonl_path + ".meta")
    os.replace(json_path, json_path + ".bak")     # backup, nu stergem direct
    return added_total


def _arg_value(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    dry_run = "--dry-run" in sys.argv
    work_dir = _arg_value("--dir", CACHE_DIR)   # implicit cachedb/ (sau --dir / BINANCE_CACHE_DIR)
    # cache_price_long_trend.json e cache-ul PriceLongTrend (full-rewrite json, NU
    # per-simbol append) → ramane .json, il excludem din migrare.
    EXCLUDE = {"long_trend"}
    files = sorted(glob.glob(os.path.join(work_dir, "cache_price_*.json")))
    files = [f for f in files
             if not f.endswith(".bak")
             and os.path.basename(f)[len("cache_price_"):-len(".json")] not in EXCLUDE]
    if not files:
        print("Nu exista fisiere cache_price_*.json de migrat.")
        return
    print(f"{'DRY-RUN' if dry_run else 'MIGRARE'}: {len(files)} fisiere\n")
    for f in files:
        try:
            migrate_file(f, dry_run=dry_run)
        except Exception as e:
            print(f"[EROARE] {os.path.basename(f)}: {e}")
    if not dry_run:
        print("\nGata. Fisierele .json au fost redenumite in .json.bak "
              "(sterge-le dupa ce verifici .jsonl).")


if __name__ == "__main__":
    main()
