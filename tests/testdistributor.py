import unittest

from monitortrades_legacy import ProcentDistributor


class TestProcentDistributor(unittest.TestCase):
    def setUp(self):
        self.distributor = ProcentDistributor(
            t1=0,
            expired_duration=600,
            max_procent=0.1,
            min_procent=0.005,
            unitate_timp=60,
        )

    def test_initialization(self):
        self.assertEqual(self.distributor.t1, 0)
        self.assertEqual(self.distributor.t2, 600)
        self.assertEqual(self.distributor.max_procent, 0.1)
        self.assertEqual(self.distributor.min_procent, 0.005)
        self.assertEqual(self.distributor.unitate_timp, 60)
        self.assertAlmostEqual(self.distributor.procent_per_unit, 0.1 / 10, places=5)

    def test_get_procent_before_t1(self):
        self.assertEqual(self.distributor.get_procent(-1), 0.1)
        self.assertEqual(self.distributor.get_procent(0), 0.1)

    def test_get_procent_after_t2(self):
        self.assertEqual(self.distributor.get_procent(601), 0.005)
        self.assertEqual(self.distributor.get_procent(1000), 0.005)

    def test_get_procent_between_t1_and_t2(self):
        self.assertAlmostEqual(self.distributor.get_procent(300), 0.05, places=5)
        self.assertAlmostEqual(self.distributor.get_procent(600), 0.005, places=5)

    def test_update_init_time(self):
        self.distributor.update_init_time(100, 500)
        self.assertEqual(self.distributor.t1, 100)
        self.assertEqual(self.distributor.t2, 600)
        self.assertEqual(self.distributor.total_units, 500 / 60)
        self.assertAlmostEqual(self.distributor.procent_per_unit, 0.1 / (500 / 60), places=5)

    def test_update_init_procent(self):
        self.distributor.update_init_procent(0.2)
        self.assertEqual(self.distributor.max_procent, 0.2)
        self.assertAlmostEqual(self.distributor.procent_per_unit, 0.2 / 10, places=5)

    def test_adjust_init_procent_by(self):
        current_price = 120
        buy_price = 100
        price_difference_ratio = (current_price - buy_price) / buy_price

        self.distributor.adjust_init_procent_by(current_price, buy_price)
        adjusted_procent = 0.1 + price_difference_ratio

        self.assertAlmostEqual(self.distributor.max_procent, max(adjusted_procent, 0.005), places=5)


if __name__ == "__main__":
    unittest.main()

