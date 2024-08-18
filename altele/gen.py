import numpy as np

# Simulare de date
valori_initiale = np.linspace(7, 0, 120)  # Array descrescător de la 7 la 0
procente_asociate = np.random.uniform(-1.5, 1.5, 120)  # Procente asociate, între -150% și 150%

# Aplicăm formula de ajustare
valori_ajustate = valori_initiale * np.minimum(2, np.maximum(0, 1 + procente_asociate))

# Afișăm primele 10 valori pentru exemplificare
#for i in range(10):
#   print(f"Valoare inițială: {valori_initiale[i]:.2f}, Procent asociat: {procente_asociate[i]*100:.1f}%, Valoare ajustată: {valori_ajustate[i]:.2f}")

import numpy as np

# Simulare de date
valori_initiale = np.linspace(7, 0, 120)  # Array descrescător de la 7 la 0
procente_asociate = np.random.uniform(-1.0/10, 1.0/10, 120)  # Procente asociate, între -100% și 100%

# Factor de scalare
n = len(valori_initiale)

# Aplicăm formula de ajustare exponențială inversată cu limite
valori_ajustate =  valori_initiale * np.minimum(2, np.maximum(0, 1 + np.exp(-np.arange(120) / n) * procente_asociate))

# Afișăm primele 10 valori pentru exemplificare
example_values_inverse_exp = [(valori_initiale[i], procente_asociate[i]*100, valori_ajustate[i]) for i in range(120)]
for i in range(110):
    print(f"Valoare inițială: {example_values_inverse_exp[i][0]:.2f}, Procent asociat: {example_values_inverse_exp[i][1]:.1f}%, Valoare ajustată: {example_values_inverse_exp[i][2]:.2f}")



