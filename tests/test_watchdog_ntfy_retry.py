"""watchdog_common.send_ntfy: pe 429 (rate-limit ntfy.sh in burst) reincearca o data
respectand Retry-After, in loc sa piarda mesajul. Fara retry, un burst din flota ducea
la 'push ESUAT 429' si alerte nelivrate."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "verify_tools"))

import watchdog_common as wc


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class SendNtfyRetryTest(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("NTFY_TOPIC_ERROR")
        os.environ["NTFY_TOPIC_ERROR"] = "testtopic"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("NTFY_TOPIC_ERROR", None)
        else:
            os.environ["NTFY_TOPIC_ERROR"] = self._prev

    def test_retry_on_429_then_success(self):
        with mock.patch("requests.post") as mpost, mock.patch("time.sleep") as msleep:
            mpost.side_effect = [_Resp(429, {"Retry-After": "1"}), _Resp(200)]
            ok = wc.send_ntfy("titlu", "mesaj")
        self.assertTrue(ok)
        self.assertEqual(mpost.call_count, 2)     # a reincercat
        msleep.assert_called_once()               # a asteptat Retry-After

    def test_429_persists_returns_false_bounded(self):
        with mock.patch("requests.post") as mpost, mock.patch("time.sleep"):
            mpost.side_effect = [_Resp(429), _Resp(429)]
            ok = wc.send_ntfy("titlu", "mesaj")
        self.assertFalse(ok)
        self.assertEqual(mpost.call_count, 2)     # exact 1 retry, marginit

    def test_success_first_try_no_sleep(self):
        with mock.patch("requests.post") as mpost, mock.patch("time.sleep") as msleep:
            mpost.side_effect = [_Resp(200)]
            ok = wc.send_ntfy("titlu", "mesaj")
        self.assertTrue(ok)
        self.assertEqual(mpost.call_count, 1)
        msleep.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
