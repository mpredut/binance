import logging
import builtins

# Configurare de bază pentru logging
logging.basicConfig(
    level=logging.INFO,  # Setează nivelul de logging dorit
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("bot.log"),  # Scrie logurile într-un fișier
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
    
    #logger.info(message)
    # Scrie mesajul în fișierul de log direct fără a folosi logger.info
    with open("bot.log", "a") as log_file:
        log_file.write(message + "\n")

# Redefinește funcția print din builtins pentru a funcționa în întreg codul
builtins.print = print
