import unittest

from market_regime import MarketRegimeEvaluator, MarketRegimeService


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

    def test_common_service_derives_regime_from_provider_ohlc(self):
        class Provider:
            name = "Kraken"
            def ohlc_closes(self, _symbol, _interval):
                return [100, 101, 102, 103, 104, 105]

        service = MarketRegimeService(2.0, cache_ttl_sec=30)
        decision = service.evaluate_provider(
            Provider(), "HYPEUSD", interval_min=1, window_seconds=360)
        self.assertEqual(decision.regime, "bull")
        self.assertEqual(decision.n_samples, 6)

    def test_common_service_is_bounded_and_unknown_on_provider_error(self):
        class Provider:
            name = "Hyperliquid"
            def ohlc_closes(self, _symbol, _interval):
                raise RuntimeError("offline")

        service = MarketRegimeService(cache_max=1)
        decision = service.evaluate_provider(Provider(), "HYPE", interval_min=1)
        self.assertEqual(decision.regime, "unknown")
        self.assertEqual(decision.reason, "source_error:RuntimeError")

    def test_short_falls_back_from_snapshot_to_same_provider_ohlc(self):
        class Provider:
            name = "Binance"
            def ohlc_closes(self, _symbol, interval):
                return [100, 101, 102, 103] if interval == 1 else []

        decision = MarketRegimeService().resolve(
            Provider(), "TAOUSDC", horizon="short", snapshot={})
        self.assertEqual((decision.regime, decision.horizon, decision.source),
                         ("bull", "short", "ohlc:1m"))
        self.assertTrue(decision.fallback_used)

    def test_long_uses_daily_fallback_when_four_hour_source_fails(self):
        class Provider:
            name = "Kraken"
            def ohlc_closes(self, _symbol, interval):
                if interval == 240:
                    raise RuntimeError("4h unavailable")
                return list(range(100, 130))

        decision = MarketRegimeService().resolve(
            Provider(), "HYPEUSD", horizon="long")
        self.assertEqual((decision.regime, decision.horizon, decision.source),
                         ("bull", "long", "ohlc:1440m"))
        self.assertTrue(decision.fallback_used)


if __name__ == "__main__":
    unittest.main()
