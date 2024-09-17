import logging
import builtins
import os
import sys
import datetime

process_id = os.getpid()

# Configurare de bază pentru logging
logging.basicConfig(
    level=logging.INFO,  # Setează nivelul de logging dorit
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(f"bot{process_id}.log"),  # Scrie logurile într-un fișier specific ID-ului de proces
        logging.StreamHandler()  # Afișează logurile în consola
    ]
)

logger = logging.getLogger(__name__)  # Obține un logger

# Salvează funcția originală print
original_print = builtins.print


# Obține numele aplicației Python
app_name = os.path.splitext(os.path.basename(__file__))[0]
app_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]


process_id = os.getpid()

current_date = datetime.datetime.now().strftime("%Y-%m-%d")

# Redefinește funcția print pentru a adăuga logare
def print(*args, **kwargs):
    global current_date  # Facem referire la variabila globală
    
    # Convertește toate argumentele în stringuri și le unește într-un singur mesaj
    message = " ".join(map(str, args))
    
    new_date = datetime.datetime.now().strftime("%Y-%m-%d")
    if new_date != current_date:
        current_date = new_date
    
    bot_folder = "bott"
    if not os.path.exists(bot_folder):
        os.makedirs(bot_folder)
    
    # Construiește calea completă a fișierului de log, incluzând numele aplicației, data curentă și ID-ul procesului
    log_file_path = os.path.join(os.getcwd(), bot_folder, f"{app_name}_{current_date}_pid{process_id}.log")
    
    # Obține ora și minutul curent
    current_time = datetime.datetime.now().strftime("%H:%M")

    # Apelează funcția print originală
    original_print(f"{current_time} {message}", **kwargs)
    # Scrie mesajul în fișierul de log
    try:
        with open(log_file_path, "a") as log_file:
            log_file.write(f"{current_time} {message}\n")
    except PermissionError as e:
        original_print(f"Error writing log: {e}")



# Redefinește funcția print din builtins pentru a funcționa în întreg codul
builtins.print = print
