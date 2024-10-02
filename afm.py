from plyer import notification
import requests
import hashlib
import time
import os
import platform

# Funcție pentru a genera hash-ul paginii
def get_page_hash(url):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            page_content = response.content
            return hashlib.md5(page_content).hexdigest()
        else:
            print(f"Nu am putut accesa pagina. Status code: {response.status_code}")
            return None
    except Exception as e:
        print(f"Eroare la accesarea paginii: {e}")
        return None

# Funcție pentru a afișa o notificare multiplatformă
def show_notification(title, text):
    notification.notify(
        title=title,
        message=text,
        timeout=10  # Durata notificării în secunde
    )

# Funcție pentru a reda un sunet de alertă specific pentru Windows
def play_sound():
    if platform.system() == "Windows":
        # Sunet de alertă pe Windows (folosind un sunet standard de pe Windows)
        os.system('powershell -c "(New-Object Media.SoundPlayer \\"C:\\Windows\\Media\\chord.wav\\").PlaySync()"')
    elif platform.system() == "Linux":
        # Sunet pe Linux
        os.system('play --no-show-progress --null --channels 1 synth %s sine %f' % (0.1, 440))
    elif platform.system() == "Darwin":
        # Sunet pe macOS
        os.system('afplay /System/Library/Sounds/Glass.aiff')
    else:
        # Pe Android sau alte platforme, nu facem nimic (se poate integra un sunet manual dacă e nevoie)
        pass

# URL-ul paginii
url = 'https://depunerefotovoltaice.afm.ro/'

# Hash-ul inițial al paginii
last_hash = get_page_hash(url)

if last_hash:
    print("Monitorizare pornită...")

    while True:
        time.sleep(1)  # Verifică la fiecare secundă
        current_hash = get_page_hash(url)
        print("Pagina s-a schimbat!")
        show_notification("Alertă!", "Pagina s-a modificat!")
        play_sound()

        if current_hash and current_hash != last_hash:
            print("Pagina s-a schimbat!")
            show_notification("Alertă!", "Pagina s-a modificat!")
            play_sound()
            last_hash = current_hash  # Actualizează hash-ul paginii
        elif not current_hash:
            print("Eroare la preluarea paginii. Reîncercăm...")

