import logging
import builtins
import os
import sys
import datetime

process_id = os.getpid()

# Configurare de baza pentru logging
#logging.basicConfig(
#    level=logging.INFO,  # Seteaza nivelul de logging dorit
#    format='%(asctime)s - %(levelname)s - %(message)s',
#    datefmt='%Y-%m-%d %H:%M:%S',
#    handlers=[
#        logging.FileHandler(f"bot{process_id}.log"),  # Scrie logurile într-un fisier specific ID-ului de proces
#        logging.StreamHandler()  # Afiseaza logurile în consola
#    ]
#)

#logger = logging.getLogger(__name__)  # Obtine un logger

# Salveaza functia originala print
original_print = builtins.print


# Obtine numele aplicatiei Python
app_name = os.path.splitext(os.path.basename(__file__))[0]
app_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]


process_id = os.getpid()

current_date = datetime.datetime.now().strftime("%Y-%m-%d")

# Redefineste functia print pentru a adauga logare
def print(*args, **kwargs):
    global current_date  # Facem referire la variabila globala
    
    # Converteste toate argumentele în stringuri si le uneste într-un singur mesaj
    message = " ".join(map(str, args))
    
    new_date = datetime.datetime.now().strftime("%Y-%m-%d")
    if new_date != current_date:
        current_date = new_date
    
    bot_folder = "bot_logger"
    if not os.path.exists(bot_folder):
        os.makedirs(bot_folder)
    
    # Construieste calea completa a fisierului de log, incluzand numele aplicatiei, data curenta si ID-ul procesului
    log_file_path = os.path.join(os.getcwd(), bot_folder, f"{app_name}_{current_date}_pid{process_id}.log")
    
    # Obtine ora si minutul curent
    current_time = datetime.datetime.now().strftime("%H:%M")

    # Apeleaza functia print originala
    original_print(f"{current_time} {message}", **kwargs)
    # Scrie mesajul în fisierul de log
    try:
        with open(log_file_path, "a") as log_file:
            log_file.write(f"{current_time} {message}\n")
    except PermissionError as e:
        original_print(f"Error writing log: {e}")



# Redefineste functia print din builtins pentru a functiona în întreg codul
builtins.print = print
