"""Utilitar istoric manual; nu face parte din runtime."""

from plyer import notification
import requests
import hashlib
import time
import utils  # Presupunem ca utils.py contine functia beep

# Functie pentru a genera hash-ul paginii
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

# Functie pentru a afisa o notificare multiplatforma
def show_notification(title, text):
    notification.notify(
        title=title,
        message=text,
        timeout=10  # Durata notificarii in secunde
    )

# Functie pentru a reda sunetul folosind utils.beep
def play_sound():
    try:
        utils.beep(3)  # Apelam functia beep din utils.py
    except Exception as e:
        print(f"Eroare la redarea sunetului: {e}")

# URL-ul paginii
url = 'https://depunerefotovoltaice.afm.ro/'

# Hash-ul initial al paginii
last_hash = get_page_hash(url)

if last_hash:
    print("Monitorizare pornita...")

    while True:
        time.sleep(1)  # Verifica la fiecare secunda
        current_hash = get_page_hash(url)
        print(f"Nimic")
        #show_notification("Alerta!", "Pagina s-a modificat!")
        #play_sound()
        if current_hash and current_hash != last_hash:
            print("Pagina s-a schimbat!............................................HAHA!")
            #show_notification("Alerta!", "Pagina s-a modificat!")
            play_sound()
            last_hash = current_hash  # Actualizeaza hash-ul paginii
            exit
        elif not current_hash:
            print("Eroare la preluarea paginii. Reincercam...")
