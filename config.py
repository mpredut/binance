import time
import threading

# Cache pentru configurări
config_cache = {}

# Calea către fișierul de configurare
config_file_path = "config.txt"

def load_config():
    """
    Încarcă fișierul de configurare și actualizează cache-ul.
    """
    global config_cache
    try:
        with open(config_file_path, "r") as file:
            lines = file.readlines()
            new_config = {}
            for line in lines:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    # Convertim valori tip "true"/"false" în boolean
                    if value.lower() == "true":
                        value = True
                    elif value.lower() == "false":
                        value = False
                    new_config[key] = value
            config_cache = new_config
            print("Config actualizat:", config_cache)
    except FileNotFoundError:
        print(f"Fișierul {config_file_path} nu a fost găsit.")

def config_watcher(interval= 5 * 60): #5 minute
    """
    Monitorizează periodic fișierul de configurare și reîncarcă cache-ul.
    """
    while True:
        load_config()
        time.sleep(interval)

def is_trade_enabled():
    """
    Verifică dacă atributul `trade_enabled` este setat la True în cache.
    """
    return config_cache.get("trade_enabled", False)

# Pornim un thread pentru monitorizarea configurației
watcher_thread = threading.Thread(target=config_watcher, daemon=True)
watcher_thread.start()

# Exemplu de utilizare
if __name__ == "__main__":
    print("Monitorizarea fișierului de configurare...")
    try:
        while True:
            # Demonstrație: verificăm dacă trading-ul este activat
            print("Trade Enabled:", is_trade_enabled())
            time.sleep(10)
    except KeyboardInterrupt:
        print("Monitorizare oprită.")
