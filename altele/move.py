import os
import shutil
import json

import os
import shutil
import json

def backup_and_replace(converted_file: str, cache_file: str):
    """
    1. Mută fișierul existent cache_file în old/ ca backup (dacă există)
    2. Mută/redenumește converted_file în cache_file
    """
    old_folder = "old"
    os.makedirs(old_folder, exist_ok=True)

    # dacă există fișierul cache, îl mutăm în old/
    if os.path.exists(cache_file):
        backup_path = os.path.join(old_folder, os.path.basename(cache_file))
        shutil.move(cache_file, backup_path)
        print(f"[INFO] Fișierul vechi {cache_file} mutat în {backup_path}")

    # mutăm fișierul convertit în locul fișierului cache
    shutil.move(converted_file, cache_file)
    print(f"[INFO] Fișierul convertit {converted_file} mutat / redenumit în {cache_file}")


backup_and_replace("btc_converted.json", "cache_price_BTCUSDC.json")
backup_and_replace("tao_converted.json", "cache_price_TAOUSDC.json")
