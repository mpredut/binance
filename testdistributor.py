import time
import unittest

class ProcentDistributor:
    def __init__(self, t1, expired_duration, max_procent, min_procent = 0.008, unitate_timp=60):
        if max_procent < min_procent:
            raise ValueError(f"max_procent ({max_procent}) cannot be smaller than min_procent ({min_procent})")
        self.procent = max_procent #TOTO remove self.
        self.max_procent = max_procent
        self.min_procent = min_procent
        self.unitate_timp = unitate_timp
        self.update_period_time(t1, expired_duration)      
        self.update_max_procent(max(max_procent, min_procent))
        
    def get_procent(self, current_time):
        if current_time < self.t1:
            return self.max_procent
        if current_time > self.t2:
            print(f"current_time {current_time} > self.t2 {self.t2}")
            return max(0, self.min_procent)
        units_passed = (current_time - self.t1) / self.unitate_timp
        print(f"units_passed: {units_passed}")
        print(f"procent_per_unit: {self.procent_per_unit}")
        return max(self.max_procent - (units_passed * self.procent_per_unit), self.min_procent)
    
    def get_procent_by(self, current_time, current_price, buy_price):
        if current_time < self.t1:
            return self.procent
        if current_time > self.t2:
            return max(0, self.min_procent)
        self.procent = self.calculate_procent_by(current_price, buy_price) #TOTO remove self.
        units_passed = (current_time - self.t1) / self.unitate_timp
        procent_per_unit = procent / self.total_units
        return max(procent - (units_passed * self.procent_per_unit), self.min_procent)
    
    def update_period_time(self, t1, expired_duration):
        self.t1 = t1
        self.t2 = self.t1 + max(expired_duration, 1)
        self.total_units = (self.t2 - self.t1) / self.unitate_timp
   
    def update_max_procent(self, procent):
        if procent is not None:
            self.max_procent = procent
            self.procent_per_unit = self.max_procent / self.total_units
      
    def calculate_procent_by(self, current_price, buy_price):
        price_difference_percentage = ((current_price - buy_price) / buy_price)
        procent_desired_profit = self.max_procent
        procent_desired_profit += price_difference_percentage
        procent_desired_profit = max(procent_desired_profit, self.min_procent) #TODO: review if max
        print(f"adjust_init_procent_by: {procent_desired_profit}")
        return procent_desired_profit

class TestProcentDistributor(unittest.TestCase):

    def setUp(self):
        # Inițializează un obiect ProcentDistributor pentru a fi utilizat în testele următoare
        self.distributor = ProcentDistributor(t1=0, expired_duration=600, init_pt=0.1, min_pt=0.005, unitate_timp=60)

    def test_initialization(self):
        # Verificăm dacă obiectul se inițializează corect
        self.assertEqual(self.distributor.t1, 0)
        self.assertEqual(self.distributor.t2, 600)
        self.assertEqual(self.distributor.init_pt, 0.1)
        self.assertEqual(self.distributor.min_pt, 0.005)
        self.assertEqual(self.distributor.unitate_timp, 60)
        self.assertAlmostEqual(self.distributor.procent_per_unit, 0.1 / 10, places=5)

    def test_get_procent_before_t1(self):
        # Testăm dacă procentul rămâne constant înainte de t1
        self.assertEqual(self.distributor.get_procent(-1), 0.1)
        self.assertEqual(self.distributor.get_procent(0), 0.1)

    def test_get_procent_after_t2(self):
        # Testăm dacă procentul scade la minim după t2
        self.assertEqual(self.distributor.get_procent(601), 0.005)
        self.assertEqual(self.distributor.get_procent(1000), 0.005)

    def test_get_procent_between_t1_and_t2(self):
        # Testăm dacă procentul scade corespunzător între t1 și t2
        self.assertAlmostEqual(self.distributor.get_procent(300), 0.05, places=5)  # La jumătatea intervalului
        self.assertAlmostEqual(self.distributor.get_procent(600), 0.005, places=5)  # La finalul intervalului

    def test_update_init_time(self):
        # Testăm dacă timpul se actualizează corect
        self.distributor.update_init_time(100, 500)
        self.assertEqual(self.distributor.t1, 100)
        self.assertEqual(self.distributor.t2, 600)
        self.assertEqual(self.distributor.total_units, 500 / 60)
        self.assertAlmostEqual(self.distributor.procent_per_unit, 0.1 / (500 / 60), places=5)

    def test_update_init_procent(self):
        # Testăm dacă procentul inițial și procentul pe unitate se actualizează corect
        self.distributor.update_init_procent(0.2)
        self.assertEqual(self.distributor.init_pt, 0.2)
        self.assertAlmostEqual(self.distributor.procent_per_unit, 0.2 / 10, places=5)

    def test_adjust_init_procent_by(self):
        # Testăm ajustarea procentului în funcție de diferența de preț
        current_price = 120
        buy_price = 100
        price_difference_percentage = ((current_price - buy_price) / buy_price) * 100

        self.distributor.adjust_init_procent_by(current_price, buy_price)
        adjusted_procent = 0.1 + price_difference_percentage  # Valoarea dorită

        self.assertAlmostEqual(self.distributor.init_pt, max(adjusted_procent, 0.005), places=5)

    def test_get_procent_over_time(self):
        current_price = 120
        buy_price = 100
        # Parcurgem intervalul de timp și verificăm procentul
        print("Timp | Procent")
        for time in range(-10, 800, 5):  # De la -10 la 620, cu un pas de 50 (inclusiv un pic după t2)
            procent = self.distributor.get_procent(time)
            print(f"{time:>4} | {procent:.5f}")
            if time == 0:
                self.distributor.adjust_init_procent_by(current_price, buy_price)
            procent = self.distributor.get_procent(time)
            print(f"next {time:>4} | {procent:.5f}")
            # Testăm limitele cunoscute
            #if time < 0:
            #    self.assertEqual(procent, 0.1)  # Ar trebui să fie procentul inițial
            #elif time > 600:
            #    self.assertEqual(procent, 0.005)  # Ar trebui să fie procentul minim
            #else:
                # Verificăm dacă procentul este între procentul inițial și minim
            #    self.assertTrue(0.005 <= procent <= 0.1)


if __name__ == '__main__':
    from datetime import datetime, timedelta

    hours = 24  # Exemplu de valoare pentru ore
    cutoff_time = datetime.now().timestamp() - timedelta(hours=hours).total_seconds()

    unittest.main()

