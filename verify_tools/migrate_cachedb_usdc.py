#!/usr/bin/env python3
"""Normalize legacy USDT names that represent the current USDC policy.

Modify only known, safe forms:
  * the total_value_usdt key becomes total_value_usdc;
  * legacy BTCUSDT/TAOUSDT symbols become BTCUSDC/TAOUSDC in keys and declarative
    state fields (symbol/s/pair/name).

It does not convert monetary values or rewrite the order history of other pairs.
Writes are atomic; the first apply creates a `.pre_usdc_migration` backup.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path


SYMBOL_MAP = {"BTCUSDT": "BTCUSDC", "TAOUSDT": "TAOUSDC"}
SYMBOL_FIELDS = {"symbol", "s", "pair", "name"}


def normalize(value, parent_key=None):
    changes = 0
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            new_key = "total_value_usdc" if key == "total_value_usdt" else SYMBOL_MAP.get(key, key)
            normalized, count = normalize(item, key)
            if new_key != key:
                changes += 1
            changes += count
            # The existing USDC value takes precedence over the legacy alias.
            if new_key not in result or key != "total_value_usdt":
                result[new_key] = normalized
        return result, changes
    if isinstance(value, list):
        result = []
        for item in value:
            normalized, count = normalize(item, parent_key)
            result.append(normalized)
            changes += count
        return result, changes
    if parent_key in SYMBOL_FIELDS and isinstance(value, str) and value in SYMBOL_MAP:
        return SYMBOL_MAP[value], 1
    return value, 0


def _atomic_write(path: Path, text: str):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def migrate_file(path: Path, apply=False):
    if path.suffix == ".jsonl":
        rows = []
        changes = 0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                normalized, count = normalize(json.loads(line))
                rows.append(json.dumps(normalized, separators=(",", ":"), ensure_ascii=False))
                changes += count
        text = "\n".join(rows) + ("\n" if rows else "")
    else:
        with path.open(encoding="utf-8") as handle:
            normalized, changes = normalize(json.load(handle))
        text = json.dumps(normalized, separators=(",", ":"), ensure_ascii=False) + "\n"
    if changes and apply:
        backup = Path(str(path) + ".pre_usdc_migration")
        if not backup.exists():
            shutil.copy2(path, backup)
        _atomic_write(path, text)
    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / "cachedb"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonl", ".meta"}:
            continue
        try:
            changes = migrate_file(path, apply=args.apply)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if changes:
            total += changes
            print(f"{'MIGRATE' if args.apply else 'WOULD_MIGRATE'} {path}: {changes}")
    print(f"changes={total} mode={'apply' if args.apply else 'dry-run'}")


if __name__ == "__main__":
    main()
