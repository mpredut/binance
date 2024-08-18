import logging

# Configurare de bază pentru logging
logging.basicConfig(
    level=logging.DEBUG,  # Setează nivelul de logging dorit
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("trading_bot.log"),  # Scrie logurile într-un fișier
        logging.StreamHandler()  # Afișează logurile în consola
    ]
)

logger = logging.getLogger(__name__)  # Obține un logger

# Salvează funcția originală print pentru a o putea folosi în continuare
original_print = print

# Redefinește funcția print pentru a folosi logging
def print(message, level="info", *args, **kwargs):
    """
    Un înlocuitor pentru funcția print care utilizează logging.
    
    :param message: Mesajul care va fi logat.
    :param level: Nivelul de logging ('debug', 'info', 'warning', 'error', 'critical').
    """
    #original_print("SSSSSSSSSSSSSS")
    if level == "debug":
        logger.debug(message)
    elif level == "info":
        logger.info(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    elif level == "critical":
        logger.critical(message)
    else:
        logger.info(message)  # Default to info level if no level is provided

    # Dacă vrei totuși să afișezi mesajul în consolă folosind comportamentul clasic al print-ului:
    original_print(message, *args, **kwargs)
