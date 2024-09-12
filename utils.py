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
 
def calculate_difference_percent(val1, val2):
    return abs(val1 - val2) / ((val1 + val2) / 2) * 100


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
            return True#, iteration, tolerance

        tolerance += tolerance_step
        iteration += 1

    return False#, iteration, tolerance
    
    
    from datetime import datetime

def convert_timestamp_to_human_readable(timestamp_ms):
    # Convertim timpul din milisecunde în secunde
    timestamp_sec = timestamp_ms / 1000.0
    
    # Convertim în format datetime
    human_readable_time = datetime.utcfromtimestamp(timestamp_sec)
    
    # Returnăm timpul ca string în format citibil
    return human_readable_time.strftime('%Y-%m-%d %H:%M:%S')

def convert_seconds_to_days(max_age_seconds):
    # Definim numărul de secunde într-o zi
    seconds_in_a_day = 86400  # 24 ore * 60 minute * 60 secunde
    
    # Calculăm numărul de zile
    days = max_age_seconds / seconds_in_a_day
    
    return days

    """
    Gradually decreases the percentage asymptotically as `passs` increases.
    Once `passs` reaches a point where expired_duration * passs > half_life_duration 
    (24 hours as the default constant), the percentage will decrease to half of its initial value.
    This decrease continues as `passs` increases, causing the percentage to approach zero
    asymptotically but never fully reach zero.

    :param initial_procent: The initial percentage (e.g., 0.7 for 7%)
    :param expired_duration: The duration in seconds for which the percentage should decrease
    :param passs: The variable that grows over time and influences the percentage decrease
    :param half_life_duration: The default value after which the percentage is halved (24 hours in seconds)
    :return: The adjusted percentage based on `passs`
    """
def asymptotic_decrease(initial_procent, expired_duration, passs, half_life_duration=24*60*60):
    k = expired_duration / half_life_duration  # Calculate the constant k
    return initial_procent / (1 + k * passs)  # Asymptotic decrease formula
    """
    Decreases the percentage exponentially as `passs` increases like asymptotic_decrease but exponential.
    """

def exponential_decrease(initial_procent, expired_duration, passs, half_life_duration=24*60*60):

    T = half_life_duration / expired_duration  # Calculate the time constant T
    #return initial_procent * (2 ** (-passs / T))  # Exponential decrease formula
    return initial_procent * math.exp(-passs / T)  # Exponential decrease formula using e