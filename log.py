import logging
import builtins
import os

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

# Redefinește funcția print pentru a adăuga logare
def print(*args, **kwargs):
    # Convertește toate argumentele în stringuri și le unește într-un singur mesaj
    message = " ".join(map(str, args))
    
    # Apelează funcția print originală
    original_print(message, **kwargs)
    
    bot_folder = "bot"
    if not os.path.exists(bot_folder):
        os.makedirs(bot_folder)
    
    # Scrie mesajul în fișierul de log din folderul "bot"
    #logger.info(message)
-   # Scrie mesajul în fișierul de log direct fără a folosi logger.info
    with open(f"{bot_folder}/bot{process_id}.log", "a") as log_file:
        log_file.write(message + "\n")


# Redefinește funcția print din builtins pentru a funcționa în întreg codul
builtins.print = print
