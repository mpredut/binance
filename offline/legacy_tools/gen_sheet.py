"""Generator istoric de raport; nu face parte din runtime."""

import pandas as pd

from datetime import datetime

 

# Functie pentru generarea fisei de pontaj

def genereaza_fisa_pontaj(luna, an):

    zile_luna = pd.date_range(start=f'{an}-{luna}-01', end=f'{an}-{luna}-{pd.Timestamp(f"{an}-{luna}-01").days_in_month}')

   

    # Template pentru pontaj, adaptat conform modelului tau

    pontaj_data = {

        'Data': zile_luna.strftime('%d.%m.%Y'),

        'Ziua': zile_luna.strftime('%A'),

        'Ora Inceput': ['09:00' for _ in range(len(zile_luna))],  # Poti ajusta aceste valori

        'Ora Sfarsit': ['17:00' for _ in range(len(zile_luna))],  # Poti ajusta aceste valori

        'Ore lucrate': [8 for _ in range(len(zile_luna))],  # Poti ajusta numarul de ore

        'Observatii': ['' for _ in range(len(zile_luna))]

    }

   

    df = pd.DataFrame(pontaj_data)

   

    # Salvare fisier Excel cu numele specificat

    file_name = f'fisa_pontaj_{luna}_{an}.xlsx'

    df.to_excel(file_name, index=False)

    print(f'Fisa de pontaj pentru {luna}/{an} a fost generata: {file_name}')

 

# Exemplo de utilizare

genereaza_fisa_pontaj(9, 2024)

