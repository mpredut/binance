"""Order-safe retry mounting shared by the provider HTTP clients."""

import unittest

import requests

from providers.http_resilience import mount_connect_retry


class HttpResilienceTest(unittest.TestCase):
    def _retry(self, **kw):
        session = requests.Session()
        self.assertTrue(mount_connect_retry(session, **kw))
        return session.get_adapter("https://api.example.com").max_retries

    def test_connect_only_is_the_default_and_never_retries_read_or_status(self):
        # A POST that may already have placed an order must never be replayed, so the
        # default profile retries ONLY pre-send connection failures (DNS/connect).
        retry = self._retry()
        self.assertEqual(retry.connect, 3)
        self.assertEqual(retry.read, 0)
        self.assertEqual(retry.status, 0)

    def test_both_schemes_share_the_mounted_adapter(self):
        session = requests.Session()
        mount_connect_retry(session)
        self.assertIs(session.get_adapter("https://x").max_retries,
                      session.get_adapter("http://x").max_retries)

    def test_idempotent_profile_adds_read_and_status_but_excludes_post(self):
        # The python-binance profile: GET-class requests retry on read errors and
        # retryable statuses, but POST (order placement) stays connect-only through
        # urllib3's idempotent-method default.
        retry = self._retry(attempts=3, backoff=1.0, read=3,
                            status_forcelist=[429, 502, 503, 504], raise_on_status=True)
        self.assertEqual(retry.connect, 3)
        self.assertEqual(retry.read, 3)
        self.assertEqual(sorted(retry.status_forcelist), [429, 502, 503, 504])
        self.assertTrue(retry.raise_on_status)
        allowed = (getattr(retry, "allowed_methods", None)
                   or getattr(retry, "method_whitelist", None))
        self.assertIsNotNone(allowed, "urllib3 Retry should expose an idempotent set")
        self.assertNotIn("POST", {str(m).upper() for m in allowed})

    def test_best_effort_returns_false_without_raising(self):
        class _BadSession:
            def mount(self, *_a, **_k):
                raise RuntimeError("cannot mount")

        self.assertFalse(mount_connect_retry(_BadSession()))


if __name__ == "__main__":
    unittest.main()
