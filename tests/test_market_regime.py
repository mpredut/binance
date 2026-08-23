import unittest

from market_regime import MarketRegimeEvaluator


class MarketRegimeEvaluatorTest(unittest.TestCase):
    def setUp(self):
        self.evaluator = MarketRegimeEvaluator(2.0)

    def test_direction_and_strength_are_provider_neutral(self):
        bull = self.evaluator.evaluate({
            "gradient_recent": 0.5, "epsilon": 0.1,
            "n_samples": 40, "window_seconds": 900,
        })
        bear = self.evaluator.evaluate({"gradient_recent": -0.5, "epsilon": 0.1})
        flat = self.evaluator.evaluate({"gradient_recent": 0.2, "epsilon": 0.1})

        self.assertEqual((bull.regime, bull.strength), ("bull", 5.0))
        self.assertEqual(bear.regime, "bear")
        self.assertEqual(flat.regime, "sideways")
        self.assertEqual((bull.n_samples, bull.window_seconds), (40, 900.0))

    def test_exposure_adversity_is_symmetric(self):
        bull = self.evaluator.evaluate({"gradient_recent": 0.5, "epsilon": 0.1})
        bear = self.evaluator.evaluate({"gradient_recent": -0.5, "epsilon": 0.1})
        self.assertTrue(bear.adverse_to("LONG"))
        self.assertTrue(bull.adverse_to("SOLD"))
        self.assertFalse(bull.adverse_to("LONG"))
        self.assertFalse(bear.adverse_to("SOLD"))

    def test_unavailable_and_invalid_signals_are_explicit_unknown(self):
        self.assertEqual(self.evaluator.evaluate(None).regime, "unknown")
        invalid = self.evaluator.evaluate({"gradient_recent": "bad", "epsilon": 1})
        self.assertEqual((invalid.regime, invalid.fresh, invalid.reason),
                         ("unknown", False, "invalid_signal"))


if __name__ == "__main__":
    unittest.main()
