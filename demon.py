import os
import sys
import subprocess
import time
import json

REFERENCE_FILE = "demon_reference.json"
LOG_FILE = "demon.log"

PYTHON_PATH = sys.executable
CURRENT_PATH = os.getcwd()


def ensure_absolute_paths(command):
    parts = command.split(None)
    if len(parts) < 2:
        print(f"Comanda '{command}' trebuie să fie în formatul '<PATH> <NAME>'")
        return command
    
    script_name = parts[1]
    if not os.path.isabs(script_name):
        script_path = os.path.join(CURRENT_PATH, script_name)
    else:
        script_path = script_name  # Dacă este deja absolută, nu se modifică

    python_path = parts[0]
    if not os.path.isabs(python_path):
        python_path = PYTHON_PATH  # Folosește PYTHON_PATH doar dacă parts[0] nu este absolut

    # Construieste comanda finală
    new_command = f"{python_path} {script_path}"
    
    return new_command


def remove_absolute_paths(command):
    parts = command.split(None)
    
    if len(parts) < 2:
        print(f"Comanda '{command}' trebuie să fie în formatul '<EXECUTABLE> <SCRIPT>'")
        return command
    
    executable = parts[0]
    if os.path.isabs(executable):
        executable = os.path.basename(executable)  # Extrage doar numele executabilului

    script_name = parts[1]
    if os.path.isabs(script_name):
        script_name = os.path.basename(script_name)  # Extrage doar numele scriptului
    
    # Construiește comanda finală fără căi absolute
    new_command = f"{executable} {script_name}"
    
    return new_command


def log_message(message):
    with open(LOG_FILE, "a") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
    print(message)

def save_reference_file(reference):
    with open(REFERENCE_FILE, "w") as ref_file:
        json.dump(reference, ref_file)
    log_message("Referința proceselor a fost salvată.")

def load_reference_file():
    if os.path.exists(REFERENCE_FILE):
        with open(REFERENCE_FILE, "r") as ref_file:
            return json.load(ref_file)
    return {}


def get_running_python_processes():
    try:
        output = subprocess.check_output(["ps", "-aux"], text=True)
        processes = [line.split(None, 10) for line in output.split("\n") if "python" in line and len(line.split(None, 10)) > 10]
        #print(processes)
        return {
            remove_absolute_paths(proc[-1]): ensure_absolute_paths(proc[-1]) for proc in processes
        }
    except subprocess.CalledProcessError as e:
        log_message(f"Eroare la executarea comenzii ps: {e}")
        return {}

def start_process(script_name, script_path):
    try:
        command_parts = script_path.split()
        subprocess.Popen(command_parts)
        #command = f"xterm -hold -e {script_path}"  # Sau "gnome-terminal -- bash -c 'python3 {script_path}; exec bash'"
        #subprocess.Popen(command, shell=True)
        log_message(f"Procesul {script_name} a fost pornit cu comanda: {script_path}")
        return script_path
    except Exception as e:
        log_message(f"Eroare la pornirea procesului {script_path}: {e}")
        return None

def start_process_decuplat(script_name, script_path):
    try:
        command_parts = script_path.split()
        subprocess.Popen(
            command_parts,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        log_message(f"Procesul {script_name} a fost pornit ca proces independent cu comanda: {script_path}")
        return script_path
    except Exception as e:
        log_message(f"Eroare la pornirea procesului {script_path}: {e}")
        return None

def monitor_processes_v0():
    reference = load_reference_file()
    first_run = not bool(reference)
    if first_run:
        log_message("Prima rulare: salvăm referința inițială a proceselor.")
        running_processes = get_running_python_processes()
        reference = {script_name: command for script_name, command in running_processes.items()}
        save_reference_file(reference)
    else:
        log_message("Monitorizare procese bazată pe referința existentă.")

    while True:
        running_processes = get_running_python_processes()
        log_message(f"Procese active: {running_processes}")
        log_message(f"Referința proc: {reference}")

        for script_name, expected_command in reference.items():
            if script_name not in running_processes:
                log_message(f"Procesul {script_name} nu este activ. Îl repornim.")
            elif running_processes[script_name] != (expected_command):
                log_message(f"Procesul {script_name} rulează cu o comandă diferită. Îl repornim.")
            else:
                # Procesul este în regulă
                continue

            # Dacă procesul lipsește sau comanda e diferită, repornim
            new_command = start_process(script_name, expected_command)
            if new_command:
                #reference[script_name] = expected_command
                reference.pop(script_name)
                reference[expected_command] = expected_command
                log_message(f"Referința proc: {reference}")
                save_reference_file(reference)

        time.sleep(10)


def monitor_processes():
    reference = load_reference_file()
    first_run = not bool(reference)
    
    if first_run:
        log_message("Prima rulare: salvăm referința inițială a proceselor.")
        running_processes = get_running_python_processes()
        reference = {script_name: command for script_name, command in running_processes.items()}
        save_reference_file(reference)
    else:
        log_message("Monitorizare procese bazată pe referința existentă.")

    while True:
        running_processes = get_running_python_processes()
        log_message(f"Procese active: {running_processes}")
        log_message(f"Referința proc: {reference}")

        # Vom crea o listă cu cheile de proces care necesită modificări
        processes_to_remove = []  # Lista de procese de eliminat din referință
        processes_to_add = []     # Lista de procese de adăugat în referință

        for script_name, expected_command in reference.items():
            if script_name not in running_processes:
                log_message(f"Procesul {script_name} nu este activ. Îl repornim.")
                processes_to_remove.append(script_name)
            elif running_processes[script_name] != expected_command:
                log_message(f"Procesul {script_name} rulează cu o comandă diferită. Îl repornim.")
                processes_to_remove.append(script_name)
            else:
                # Procesul este în regulă
                continue

            # Dacă procesul lipsește sau comanda e diferită, repornim
            new_command = start_process(script_name, expected_command)
            if new_command:
                processes_to_add.append((script_name, expected_command))

        # După ce am terminat de iterat prin procese, actualizăm referința
        changed = False
        for script_name in processes_to_remove:
            if script_name in reference:
                reference.pop(script_name)
                changed = True

        for script_name, expected_command in processes_to_add:
            reference[script_name] = expected_command
            changed = True
        
        if changed:
            save_reference_file(reference)

        time.sleep(10)


if __name__ == "__main__":
    log_message("Monitorizarea proceselor a început.")
    try:
        monitor_processes()
    except KeyboardInterrupt:
        log_message("Monitorizarea a fost oprită manual.")
