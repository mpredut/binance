import io
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import unittest
from unittest.mock import MagicMock, patch

import alertnotifiers
import botcore


def _response(status=200, body=b"ok"):
    response = MagicMock()
    response.status = status
    response.read.return_value = body
    response.__enter__.return_value = response
    return response


class SharedHttpHelpersTest(unittest.TestCase):
    def test_json_and_form_requests_share_transport_without_changing_wire_format(self):
        with patch.object(
            botcore.urllib.request, "urlopen", return_value=_response(201, b"done")
        ) as urlopen:
            result = botcore.http_post_json(
                "https://example.test/orders", {"qty": 2}, {"Authorization": "x"}
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(result, (201, b"done"))
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"qty": 2})
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Authorization"), "x")

        with patch.object(
            botcore.urllib.request, "urlopen", return_value=_response()
        ) as urlopen:
            botcore.http_post_form("https://example.test/orders", {"pair": "HYPE/USD"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.data, b"pair=HYPE%2FUSD")
        self.assertEqual(
            request.get_header("Content-type"),
            "application/x-www-form-urlencoded",
        )

    def test_http_and_transport_errors_keep_previous_fail_closed_contract(self):
        error = urllib.error.HTTPError(
            "https://example.test", 429, "rate limit", {}, io.BytesIO(b"slow down")
        )
        with patch.object(botcore.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(botcore.http_get("https://example.test"),
                             (429, b"slow down"))

        with patch.object(
            botcore.urllib.request, "urlopen", side_effect=TimeoutError("timeout")
        ), patch.object(botcore, "log"):
            self.assertEqual(botcore.http_request("DELETE", "https://example.test"),
                             (0, b""))

    def test_json_and_form_payloads_cannot_be_mixed(self):
        with self.assertRaisesRegex(ValueError, "mutual exclusive"):
            botcore.http_request(
                "POST", "https://example.test", payload={"x": 1}, form={"x": 1}
            )


class DotenvHelpersTest(unittest.TestCase):
    def test_load_and_parse_share_syntax_but_keep_their_override_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comment\n"
                "export FIRST=one # inline\n"
                "QUOTED='two # preserved'\n"
                "DUP=first\n"
                "DUP=last\n",
                encoding="utf-8",
            )

            parsed = botcore.parse_dotenv(str(path))
            self.assertEqual(parsed["FIRST"], "one")
            self.assertEqual(parsed["QUOTED"], "two # preserved")
            self.assertEqual(parsed["DUP"], "last")

            with patch.dict(os.environ, {"FIRST": "real"}, clear=True), patch.object(
                botcore, "log"
            ):
                botcore.load_dotenv(str(path))
                self.assertEqual(os.environ["FIRST"], "real")
                self.assertEqual(os.environ["QUOTED"], "two # preserved")
                self.assertEqual(os.environ["DUP"], "first")


class BoundNotifierTest(unittest.TestCase):
    def test_explicit_symbol_then_environment_priority_then_fallback(self):
        bound = alertnotifiers.bind_notify(
            ("SYMBOL_LABEL", "KRAKEN_PAIR"), "CRYPTO"
        )
        with patch.object(alertnotifiers, "notify") as shared, patch.dict(
            os.environ, {"SYMBOL_LABEL": "LABEL", "KRAKEN_PAIR": "PAIR"}, clear=False
        ):
            bound("title", "body", "source")
            self.assertEqual(shared.call_args.args[3], "LABEL")
            self.assertIsNone(shared.call_args.kwargs["email"])
            bound("title", "body", "source", symbol="EXPLICIT")
            self.assertEqual(shared.call_args.args[3], "EXPLICIT")

        with patch.object(alertnotifiers, "notify") as shared, patch.dict(
            os.environ, {}, clear=True
        ):
            bound("title", "body", "source")
            self.assertEqual(shared.call_args.args[3], "CRYPTO")


class NotificationIsolationTest(unittest.TestCase):
    def test_external_notification_kill_switch_blocks_all_delivery(self):
        with patch.dict(os.environ, {"DISABLE_EXTERNAL_NOTIFICATIONS": "true"}, clear=False), \
             patch.object(alertnotifiers.AlertNotifier, "send_phone_webhook_batch") as ntfy, \
             patch.object(alertnotifiers.AlertNotifier, "send_email_batch") as email, \
             patch.object(alertnotifiers.subprocess, "run") as desktop:
            alertnotifiers.notify(
                "STOP-LOSS test", "paper event", "pytest", "TSTX",
                desktop=True, email=True,
            )
        ntfy.assert_not_called()
        email.assert_not_called()
        desktop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
