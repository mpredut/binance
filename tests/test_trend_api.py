"""
Teste pentru trend_api — cache de trend expus extern + întârziere oportunistă
a plasării ordinelor (așteptăm preț mai bun cât timp trendul e favorabil).
"""
import time
import unittest

import trend_api


def _snap(gradient_recent, ts=None, **extra):
    s = {
        "final_trend": 1 if gradient_recent > 0 else -1,
        "gradient_recent": gradient_recent,
        "ts": ts if ts is not None else time.time(),
    }
    s.update(extra)
    return s


class TestTrendApiCache(unittest.TestCase):
    def setUp(self):
        trend_api.clear()

    def test_publish_and_get(self):
        snap = _snap(0.5)
        trend_api.publish_trend("BTCUSDT", snap)
        self.assertEqual(trend_api.get_trend_snapshot("BTCUSDT"), snap)

    def test_get_unknown_symbol_none(self):
        self.assertIsNone(trend_api.get_trend_snapshot("NOPE"))

    def test_get_all_trends(self):
        trend_api.publish_trend("A", _snap(1.0))
        trend_api.publish_trend("B", _snap(-1.0))
        allt = trend_api.get_all_trends()
        self.assertEqual(set(allt), {"A", "B"})


class TestUpdateInstant(unittest.TestCase):
    def setUp(self):
        trend_api.clear()

    def test_update_instant_creates_snapshot(self):
        trend_api.update_instant("BTCUSDT", gradient_recent=-0.4, current_price=100.0)
        snap = trend_api.get_trend_snapshot("BTCUSDT")
        self.assertEqual(snap["gradient_recent"], -0.4)
        self.assertEqual(snap["symbol"], "BTCUSDT")

    def test_update_instant_merges_over_full_snapshot(self):
        # snapshot bogat de la o evaluare completă
        trend_api.publish_trend("BTCUSDT", {
            "symbol": "BTCUSDT", "gradient_recent": 0.1,
            "slope_big": 5.0, "pos": 0, "ts": time.time(),
        })
        # update rapid per tick — schimbă doar gradientul, păstrează slope_big
        trend_api.update_instant("BTCUSDT", gradient_recent=-0.9, ts=time.time())
        snap = trend_api.get_trend_snapshot("BTCUSDT")
        self.assertEqual(snap["gradient_recent"], -0.9)   # actualizat
        self.assertEqual(snap["slope_big"], 5.0)          # păstrat din full


class TestIsFavorableToWait(unittest.TestCase):
    def setUp(self):
        trend_api.clear()

    def test_buy_waits_while_price_falling(self):
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=-0.3))
        self.assertTrue(trend_api.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_buy_stops_when_price_rising(self):
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=0.3))
        self.assertFalse(trend_api.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_sell_waits_while_price_rising(self):
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=0.3))
        self.assertTrue(trend_api.is_favorable_to_wait("SELL", "BTCUSDT"))

    def test_sell_stops_when_price_falling(self):
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=-0.3))
        self.assertFalse(trend_api.is_favorable_to_wait("SELL", "BTCUSDT"))

    def test_no_snapshot_not_favorable(self):
        self.assertFalse(trend_api.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_stale_snapshot_not_favorable(self):
        old = time.time() - trend_api.TREND_STALE_SEC - 5
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=-0.3, ts=old))
        self.assertFalse(trend_api.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_case_insensitive_side(self):
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=-0.3))
        self.assertTrue(trend_api.is_favorable_to_wait("buy", "BTCUSDT"))


class TestWaitForFavorableEntry(unittest.TestCase):
    def setUp(self):
        trend_api.clear()

    def test_returns_immediately_when_not_favorable(self):
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=0.5))  # urcă → nu aștept BUY
        calls = []
        waited = trend_api.wait_for_favorable_entry(
            "BUY", "BTCUSDT", max_wait_sec=10, poll_sec=1.0,
            sleep_fn=lambda s: calls.append(s))
        self.assertEqual(waited, 0.0)
        self.assertEqual(calls, [])

    def test_returns_immediately_when_no_snapshot(self):
        waited = trend_api.wait_for_favorable_entry(
            "BUY", "BTCUSDT", max_wait_sec=10, sleep_fn=lambda s: None)
        self.assertEqual(waited, 0.0)

    def test_waits_until_max_when_persistently_favorable(self):
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=-0.5))  # scade mereu
        slept = []
        waited = trend_api.wait_for_favorable_entry(
            "BUY", "BTCUSDT", max_wait_sec=3.0, poll_sec=1.0,
            sleep_fn=lambda s: slept.append(s))
        # a tot dormit până la deadline (favorabil constant)
        self.assertGreater(waited, 0.0)
        self.assertGreaterEqual(len(slept), 1)

    def test_stops_when_trend_flips(self):
        # favorabil la început (scade), apoi se inversează → încetează așteptarea
        trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=-0.5))
        state = {"n": 0}

        def fake_sleep(_s):
            state["n"] += 1
            if state["n"] >= 2:
                # după 2 poll-uri, prețul nu mai scade → nu mai e favorabil
                trend_api.publish_trend("BTCUSDT", _snap(gradient_recent=0.4))

        waited = trend_api.wait_for_favorable_entry(
            "BUY", "BTCUSDT", max_wait_sec=60.0, poll_sec=1.0, sleep_fn=fake_sleep)
        # s-a oprit din cauza inversării, nu a deadline-ului
        self.assertGreaterEqual(waited, 2.0)
        self.assertLess(waited, 60.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
