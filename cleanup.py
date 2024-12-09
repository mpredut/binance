import os
import time
from datetime import datetime, timedelta
import shutil

def get_disk_space(path):
    total, used, free = shutil.disk_usage(path)
    free_space = free / (1024 ** 2)  # Convert to MB
    print(f"Free space: {free_space:.2f} MB")
    return free_space

def parse_date_from_filename(filename):
    try:
        parts = filename.split("_")
        for part in parts:
            try:
                return datetime.strptime(part, "%Y-%m-%d")
            except ValueError:
                continue
    except Exception:
        return None

"""
Monitorizează și curăță fișierele dintr-un folder conform criteriilor:
- Șterge fișierele mai vechi de `max_file_age_days` zile.
- Șterge fișierele care depășesc `max_file_size_mb` MB.
- Dacă spațiul liber pe disc este sub `min_free_space_mb`, șterge cel mai vechi fișier din fiecare tip.
"""
def monitor_and_cleanup(folder_path, max_file_age_days=30, max_file_size_mb=1024, min_free_space_mb=1024):
    now = datetime.now()
    max_age = timedelta(days=max_file_age_days)
    
    # Grupați fișierele după tip
    file_groups = {}
    for filename in os.listdir(folder_path):
        if filename.endswith(".log"):
            file_type = filename.split("_")[0]  # Extrage tipul fișierului (ex: monitortrades, trade3)
            file_path = os.path.join(folder_path, filename)
            file_groups.setdefault(file_type, []).append(file_path)

    # Parcurge fiecare grup de fișiere
    for file_type, files in file_groups.items():
        files = sorted(files, key=lambda f: parse_date_from_filename(os.path.basename(f)) or now)

        for file_path in files:
            try:
                # Șterge fișierele vechi
                file_date = parse_date_from_filename(os.path.basename(file_path))
                if file_date and now - file_date > max_age:
                    os.remove(file_path)
                    print(f"DELETED old file: {file_path}. Is older than {max_age.days} days")
                    continue
                else:
                    print(f"File: {file_path} is newer than {max_age.days} days")

                # Șterge fișierele mari
                file_size_mb = os.path.getsize(file_path) / (1024 ** 2)  # Convert to MB
                if file_size_mb > max_file_size_mb:
                    os.remove(file_path)
                    print(f"DELETED large file: {file_path}. The size {file_size_mb:.4f} MB is high than {max_file_size_mb:.2f} MB")
                    continue
                else:
                    print(f"File: {file_path}. The size {file_size_mb:.4f} MB is less than {max_file_size_mb:.2f} MB")
            except Exception as e:
                print(f"Error processing file {file_path}: {e}")

        # Verifică spațiul pe disc
        if get_disk_space(folder_path) < min_free_space_mb:
            if files:
                oldest_file = files[0]  # Cel mai vechi fișier din acest grup
                try:
                    os.remove(oldest_file)
                    print(f"DELETED file to free space: {oldest_file}")
                except Exception as e:
                    print(f"Error deleting file {oldest_file}: {e}")

CURRENT_PATH = os.getcwd()
folder_to_monitor = CURRENT_PATH + "/bot_logger"

if __name__ == "__main__":
    print("Monitorizarea logurilor a început.")
    try:
        while True:
            monitor_and_cleanup(folder_to_monitor, max_file_age_days=10, max_file_size_mb=1024/600, min_free_space_mb=1024 * 60 )
            #time.sleep(24 * 60 * 60) # seconds
            time.sleep(100)
    except KeyboardInterrupt:
        print("Monitorizarea a fost oprită manual.")
