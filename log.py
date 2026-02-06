import logging
import builtins
import os
import sys
import datetime
import threading

MAX_TOTAL_LOG_SIZE = 1000 * 1024 * 1024 # 1000 MB
CHECK_EVERY_WRITES = 1000

write_counter = 0

def get_folder_size(folder):
    total = 0
    for f in os.listdir(folder):
        path = os.path.join(folder, f)
        if os.path.isfile(path):
            total += os.path.getsize(path)
    return total


def delete_oldest_log(folder):
    files = []
    for f in os.listdir(folder):
        path = os.path.join(folder, f)
        if os.path.isfile(path):
            files.append(path)

    if not files:
        return

    oldest = min(files, key=os.path.getmtime)

    try:
        os.remove(oldest)
        original_print(f"[LOGGER] deleted oldest log: {os.path.basename(oldest)}")
    except Exception as e:
        original_print(f"[LOGGER] failed to delete log: {e}")


def enforce_logger_size(folder):
    global write_counter
    write_counter += 1
    if write_counter < CHECK_EVERY_WRITES:
        return

    write_counter = 0

    try:
        total_size_bytes = get_folder_size(folder)
        total_size_kb = total_size_bytes / 1024
        max_size_kb = MAX_TOTAL_LOG_SIZE / 1024

        original_print(
            f"[LOGGER] Folder '{folder}' are {total_size_kb:.2f} KB, "
            f"maxim permis {MAX_TOTAL_LOG_SIZE / 1024:.2f} KB"
        )

        while total_size_bytes >= MAX_TOTAL_LOG_SIZE:
            original_print(
                f"[LOGGER] WARNING: Folder '{folder}' depaseste marimea maxima de "
                f"{max_size_kb:.2f} KB → voi sterge cel mai vechi fisier"
            )
            delete_oldest_log(folder)
            total_size_bytes = get_folder_size(folder)

    except Exception as e:
        original_print(f"[LOGGER] cleanup error: {e}")


# main code
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

process_id = os.getpid()

original_print = builtins.print

# Obtine numele aplicatiei Python
app_name = os.path.splitext(os.path.basename(__file__))[0]
app_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]

current_date = datetime.datetime.now().strftime("%Y-%m-%d")

lock = threading.Lock()

log_folder = "logger"
PRINT_CONTEXT = threading.local()


def print(*args, **kwargs):
    if hasattr(PRINT_CONTEXT, "enable_print") and not PRINT_CONTEXT.enable_print:
        original_print("BLOCK >>>>> BLOCK ...")
        return

    message = " ".join(map(str, args))
    new_date = datetime.datetime.now().strftime("%Y-%m-%d")

    with lock:
        global current_date
        if new_date != current_date:
            current_date = new_date

        if not os.path.exists(log_folder):
            os.makedirs(log_folder)

        log_file_path = os.path.join(
            os.getcwd(), log_folder, f"{app_name}_{current_date}_pid{process_id}.log"
        )

        current_time = datetime.datetime.now().strftime("%H:%M")

        original_print(f"{current_time} {message}", **kwargs)

        try:
            with open(log_file_path, "a") as log_file:
                log_file.write(f"{current_time} {message}\n")
        except PermissionError as e:
            original_print(f"Error writing log: {e}")

        enforce_logger_size(log_folder)

# Redefineste functia
builtins.print = print

def dumy_print(*args, **kwargs):
    pass

def disable_print():
    builtins.print = dumy_print


log_file_path = os.path.join(
    os.getcwd(), log_folder, f"{app_name}_{current_date}_pid{process_id}.log"
)


# std err wrapper
original_stderr_write = sys.stderr.write

def stderr_write(message):
    if not message.strip():
        return

    current_time = datetime.datetime.now().strftime("%H:%M")

    try:
        with open(log_file_path, "a") as log_file:
            log_file.write(f"{current_time} {message}")
    except Exception:
        pass

    original_stderr_write(message)


sys.stderr.write = stderr_write