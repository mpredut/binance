import os
import sys
import subprocess
import time
import json

# Fișier pentru a salva referința inițială a proceselor
REFERENCE_FILE = "demon_reference.json"
# Fișier pentru log-uri
LOG_FILE = "demon_monitor.log"

def log_message(message):
    """Scrie mesaje în fișierul log."""
    with open(LOG_FILE, "a") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
    print(message)

def save_reference_file(reference):
    """Salvează comanda de execuție a proceselor într-un fișier JSON."""
    with open(REFERENCE_FILE, "w") as ref_file:
        json.dump(reference, ref_file)
    log_message("Referința proceselor a fost salvată.")

def load_reference_file():
    """Încarcă referința proceselor din fișierul JSON."""
    if os.path.exists(REFERENCE_FILE):
        with open(REFERENCE_FILE, "r") as ref_file:
            return json.load(ref_file)
    return {}

def get_running_python_processes():
    """Returnează o listă cu toate procesele Python active și comenzile lor."""
    try:
        current_path = os.getcwd()  # calea curentă
        python_full_path = sys.executable
        python_path = sys.executable.replace("python", "")
        #python_path = "./"
        output = subprocess.check_output(["ps", "-aux"], text=True)
        processes = [line.split(None, 10) for line in output.split("\n") if "python" in line]
        # Extrage procesul și calea scriptului pentru fiecare proces Python
        return {proc[-1]: f"{python_path}{' '.join(proc[10:])}" for proc in processes if len(proc) > 10}
        #return {proc[-1] for proc in processes if len(proc) > 10}
    except subprocess.CalledProcessError as e:
        log_message(f"Eroare la executarea comenzii ps: {e}")
        return {}

def start_process(script_name, script_path):
    """Pornește un script Python și returnează comanda completă."""
    try:
        command_parts = script_path.split()
        process = subprocess.Popen(command_parts)
        command = f"{script_path}"
        log_message(f"Procesul {script_name} a fost pornit cu comanda: {command_parts}")
        return command
    except Exception as e:
        log_message(f"Eroare la pornirea procesului {script_path}: {e}")
        return None

def monitor_processes():
    """Monitorizează procesele Python active și repornește-le dacă sunt oprite sau modificate."""
    # Încarcă referința existentă sau creează una nouă
    reference = load_reference_file()
    first_run = not bool(reference)  # Dacă fișierul de referință e gol, e prima rulare

    if first_run:
        log_message("Prima rulare: salvăm referința inițială a proceselor.")
        # Extrage procesele Python active și construiește referința
        running_processes = get_running_python_processes()
        reference = {script_name: command for script_name, command in running_processes.items()}
        save_reference_file(reference)
    else:
        log_message("Monitorizare procese bazată pe referința existentă.")

    while True:
        running_processes = get_running_python_processes()
        log_message(f"running_processes {running_processes}")
        # Verifică procesele active și compară cu referința
        for script_name, expected_command in reference.items():
            if script_name not in running_processes : #or running_processes[script_name] != expected_command:
                log_message(f"Procesul {script_name} cu comanda{expected_command} nu respectă referința. Îl repornim.")
                log_message(f"Referinta {reference}")
                new_command = start_process(script_name, expected_command)  # Pornim procesul din nou
                if new_command:
                    reference[script_name] = expected_command  # Actualizează referința
                    save_reference_file(reference)
                    reference = load_reference_file()
        
        time.sleep(10)  # Așteaptă 10 secunde înainte de verificarea următoare

if __name__ == "__main__":
    log_message("Monitorizarea proceselor a început.")
    try:
        monitor_processes()
    except KeyboardInterrupt:
        log_message("Monitorizarea a fost oprită manual.")
