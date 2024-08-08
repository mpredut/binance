import os
import time
import math
import random
import platform

from datetime import datetime, timedelta


def beep(n):
    for _ in range(n):
        if platform.system() == 'Windows':
            import winsound
            winsound.Beep(440, 500)  # frecvența de 440 Hz, durata de 500 ms
        else:
            # Aici putem folosi o comanda de beep - nu  merge pt orice android
            os.system('echo "\007"')
        time.sleep(3)


# Bugetul inițial
budget = 1000  # USDT
order_cost_btc = 0.00004405  # BTC
max_threshold = 1.5 #% procent * 100
price_change_threshold = 0.07  # Pragul de schimbare a prețului, 0.7%
interval_time = 2 * 3600 # 2 h * 3600 seconds.
interval_time = 97 * 79

def get_interval_time(valoare_prestabilita=interval_time, marja_aleatoare=10):
    # Generarea unei valori aleatoare în intervalul [-marja_aleatoare, marja_aleatoare]
    valoare_aleatoare = random.uniform(-marja_aleatoare, marja_aleatoare)
    interval = abs(valoare_prestabilita + valoare_aleatoare)
    
    return interval
    

#valorile sunt in jurul procentului ca interval
def are_difference_equal_with_aprox_proc(value1, value2, target_percent = 10.0):
    max_iterations = random.randint(1, 100)
    if max_iterations < 1:
        max_iterations = 1
    if max_iterations > 100:
        max_iterations = 100
    # Calculează initial_tolerance ca 1% din target_percent
    initial_tolerance = target_percent * 0.01
    tolerance_step = initial_tolerance * 0.1
    #print(f"initial_tolerance {initial_tolerance}:")
    #print(f"tolerance_step {tolerance_step}:")

    #valoarea maximă a toleranței  nu depășește jumătate din target_percent
    max_tolerance = max_iterations * tolerance_step + initial_tolerance
    if max_tolerance > target_percent / 2:
        # Ajustează tolerance_step pentru a respecta limita
        tolerance_step = (target_percent / 2 - initial_tolerance) / max_iterations
        print(f"tolerance_step adjust {tolerance_step}:")

    def calculate_difference_percent(val1, val2):
        return abs(val1 - val2) / ((val1 + val2) / 2) * 100

    iteration = 0
    tolerance = initial_tolerance

    while iteration < max_iterations:
        difference_percent = calculate_difference_percent(value1, value2)
        lower_bound = target_percent - tolerance
        upper_bound = target_percent + tolerance
        #return lower_bound <= difference_percent <= upper_bound
        
        #print(f"Iteration {iteration}:")
        #print(f"  Difference percent: {difference_percent:.4f}%")
        #print(f"  Lower bound: {lower_bound:.4f}%")
        #print(f"  Upper bound: {upper_bound:.4f}%")
        
        if lower_bound <= difference_percent <= upper_bound:
            return True, iteration, tolerance

        tolerance += tolerance_step
        iteration += 1

    return False, iteration, tolerance
   

#valorile sunt aproximativ egale nu mai  mult decat procentul aproximativ
def are_values_very_close(value1, value2, target_tolerance_percent=1.0):
    max_iterations = random.randint(1, 100)
    if max_iterations < 1:
        max_iterations = 1
    if max_iterations > 100:
        max_iterations = 100
    # Calculează initial_tolerance ca 1% din target_tolerance_percent
    initial_tolerance = target_tolerance_percent * 0.01
    tolerance_step = initial_tolerance * 0.1
    #print(f"initial_tolerance {initial_tolerance}:")
    #print(f"tolerance_step {tolerance_step}:")

    #valoarea maximă a toleranței  nu depășește jumătate din target_tolerance_percent
    max_tolerance = max_iterations * tolerance_step + initial_tolerance
    if max_tolerance > target_tolerance_percent / 2:
        # Ajustează tolerance_step pentru a respecta limita
        tolerance_step = (target_tolerance_percent / 2 - initial_tolerance) / max_iterations
        print(f"tolerance_step adjust {tolerance_step}:")

    def calculate_difference_percent(val1, val2):
        return abs(val1 - val2) / ((val1 + val2) / 2) * 100

    iteration = 0
    tolerance = initial_tolerance

    while iteration < max_iterations:
        difference_percent = calculate_difference_percent(value1, value2)
        #lower_bound = target_tolerance_percent - tolerance
        upper_bound = target_tolerance_percent + tolerance
        #return lower_bound <= difference_percent <= upper_bound
        
        #print(f"Iteration {iteration}:")
        #print(f"  Difference percent: {difference_percent:.4f}%")
        #print(f"  Upper bound: {upper_bound:.4f}%")
        
        if difference_percent <= upper_bound:
            return True, iteration, tolerance

        tolerance += tolerance_step
        iteration += 1

    return False, iteration, tolerance
    