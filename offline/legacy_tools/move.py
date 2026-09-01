"""Utilitar istoric de mutare cache; opereaza pe cai relative la CWD."""

import os
import shutil
import json

def backup_and_replace(converted_file: str, cache_file: str):
    """
    1. Muta fisierul existent cache_file in old/ ca backup (daca exista)
    2. Muta/redenumeste converted_file in cache_file
    """
    old_folder = "old"
    os.makedirs(old_folder, exist_ok=True)

    # daca exista fisierul cache, il mutam in old/
    if os.path.exists(cache_file):
        backup_path = os.path.join(old_folder, os.path.basename(cache_file))
        shutil.move(cache_file, backup_path)
        print(f"[INFO] Fisierul vechi {cache_file} mutat in {backup_path}")

    # mutam fisierul convertit in locul fisierului cache
    shutil.move(converted_file, cache_file)
    print(f"[INFO] Fisierul convertit {converted_file} mutat / redenumit in {cache_file}")


backup_and_replace("btc_converted.json", "cache_price_BTCUSDC.json")
backup_and_replace("tao_converted.json", "cache_price_TAOUSDC.json")
