"""Generator istoric manual; nu face parte din runtime."""

import numpy as np

# Simulare de date
valori_initiale = np.linspace(7, 0, 120)  # Array descrescator de la 7 la 0
procente_asociate = np.random.uniform(-1.5, 1.5, 120)  # Procente asociate, intre -150% si 150%

# Aplicam formula de ajustare
valori_ajustate = valori_initiale * np.minimum(2, np.maximum(0, 1 + procente_asociate))

# Afisam primele 10 valori pentru exemplificare
#for i in range(10):
#   print(f"Valoare initiala: {valori_initiale[i]:.2f}, Procent asociat: {procente_asociate[i]*100:.1f}%, Valoare ajustata: {valori_ajustate[i]:.2f}")

import numpy as np

# Simulare de date
valori_initiale = np.linspace(7, 0, 120)  # Array descrescator de la 7 la 0
procente_asociate = np.random.uniform(-1.0/10, 1.0/10, 120)  # Procente asociate, intre -100% si 100%

# Factor de scalare
n = len(valori_initiale)

# Aplicam formula de ajustare exponentiala inversata cu limite
valori_ajustate =  valori_initiale * np.minimum(2, np.maximum(0, 1 + np.exp(-np.arange(120) / n) * procente_asociate))

# Afisam primele 10 valori pentru exemplificare
example_values_inverse_exp = [(valori_initiale[i], procente_asociate[i]*100, valori_ajustate[i]) for i in range(120)]
for i in range(110):
    print(f"Valoare initiala: {example_values_inverse_exp[i][0]:.2f}, Procent asociat: {example_values_inverse_exp[i][1]:.1f}%, Valoare ajustata: {example_values_inverse_exp[i][2]:.2f}")


