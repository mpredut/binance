"""Teste pentru providers/replay_clock.SimClock (23 iul) — extras din
tradeall_backtest._SimClock, partajat acum intre tradeall si monitortrades."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSimClock(unittest.TestCase):

    def test_callable_returns_ts(self):
        from providers.replay_clock import SimClock
        clock = SimClock()
        clock.ts = 12345.0
        self.assertEqual(clock(), 12345.0)

    def test_advancing_ts_reflected_immediately(self):
        from providers.replay_clock import SimClock
        clock = SimClock()
        clock.ts = 100.0
        self.assertEqual(clock(), 100.0)
        clock.ts = 200.0
        self.assertEqual(clock(), 200.0)

    def test_tradeall_backtest_uses_shared_class(self):
        """tradeall_backtest._SimClock trebuie sa fie ACELASI tip ca
        providers.replay_clock.SimClock (nu o reimplementare separata) —
        regresie pt extragerea de azi."""
        os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")
        import tradeall_backtest as tb
        from providers.replay_clock import SimClock
        self.assertIs(tb._SimClock, SimClock)


if __name__ == "__main__":
    unittest.main()
