import json
import os
import tempfile
import unittest
from unittest import mock

from alertnotifiers import AlertNotifier

# send_phone_webhook_batch now takes the target topic explicitly (no PHONE_ALERT_URL
# / NTFY_TOPIC env fallback). These delivery-policy tests just need any valid URL.
_WEBHOOK = "https://ntfy.sh/test-topic"


class _Response:
    status_code = 200
    headers = {}
    text = "ok"


class _RejectedResponse:
    status_code = 401
    headers = {}
    text = "unauthorized"


def _event(title="FILL BUY", body="qty=1", source="kraken"):
    return {
        "type": "bot_event", "symbol": "HYPE", "name": title,
        "source": source, "body": body,
    }


class NotificationDeliveryPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(os.environ, {
            "NOTIFICATION_STATE_FILE": os.path.join(self.temporary.name, "state.json"),
            "NTFY_TOPIC": "test-topic",
            "NTFY_DAILY_BUDGET": "2",
            "NTFY_URGENT_RESERVE": "1",
            "EMAIL_DAILY_BUDGET": "2",
            "EMAIL_URGENT_RESERVE": "1",
        }, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    @mock.patch("alertnotifiers.requests.post", return_value=_Response())
    def test_identical_ntfy_event_is_deduplicated_across_calls(self, post):
        first = AlertNotifier.send_phone_webhook_batch([_event()], webhook_url=_WEBHOOK)
        second = AlertNotifier.send_phone_webhook_batch([_event()], webhook_url=_WEBHOOK)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(post.call_count, 1)

    @mock.patch("alertnotifiers.requests.post", return_value=_Response())
    def test_normal_messages_cannot_consume_urgent_reserve(self, post):
        self.assertTrue(AlertNotifier.send_phone_webhook_batch([_event("FILL A")], webhook_url=_WEBHOOK))
        self.assertFalse(AlertNotifier.send_phone_webhook_batch([_event("FILL B")], webhook_url=_WEBHOOK))
        self.assertTrue(AlertNotifier.send_phone_webhook_batch([
            _event("ERORI WATCHDOG", source="watchdog"),
        ], webhook_url=_WEBHOOK))

        self.assertEqual(post.call_count, 2)
        with open(os.environ["NOTIFICATION_STATE_FILE"], encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["channels"]["ntfy"]["sent"], 2)

    @mock.patch("alertnotifiers._ntfy_token", return_value="stale-token")
    @mock.patch("alertnotifiers.requests.post",
                side_effect=[_RejectedResponse(), _Response()])
    def test_rejected_ntfy_token_retries_once_without_credentials(self, post, token):
        self.assertTrue(AlertNotifier.send_phone_webhook_batch([_event()], webhook_url=_WEBHOOK))
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            post.call_args_list[0].kwargs["headers"]["Authorization"],
            "Bearer stale-token")
        self.assertNotIn(
            "Authorization", post.call_args_list[1].kwargs["headers"])

    @mock.patch("alertnotifiers.smtplib.SMTP")
    def test_email_uses_same_cross_process_dedup_policy(self, smtp):
        smtp.return_value.__enter__.return_value = smtp.return_value
        alert = _event("ERORI WATCHDOG", source="watchdog")

        with mock.patch.dict(os.environ, {
            "SMTP_USERNAME": "from@example.test",
            "SMTP_PASSWORD": "secret",
            "ALERT_TO_EMAIL": "to@example.test",
        }):
            self.assertTrue(AlertNotifier.send_email_batch([alert], subject="incident"))
            self.assertTrue(AlertNotifier.send_email_batch([alert], subject="incident"))

        self.assertEqual(smtp.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
