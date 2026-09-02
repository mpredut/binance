"""Regression for the Hyperliquid hangs caused by timeout=None in the SDK."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HL_DIR = ROOT / "hyperliquid"
if str(HL_DIR) not in sys.path:
    sys.path.insert(0, str(HL_DIR))
SPEC = importlib.util.spec_from_file_location(
    "hl_client_timeout_under_test", ROOT / "hyperliquid" / "hl_client.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class _Session:
    def __init__(self):
        self.timeouts = []

    def request(self, *_args, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        return kwargs.get("timeout")


class _Api:
    def __init__(self, timeout=None):
        self.timeout = timeout
        self.session = _Session()


class HyperliquidTimeoutTest(unittest.TestCase):
    def test_timeouts_are_passed_before_sdk_constructors_run(self):
        info = _Api(timeout=module.INFO_TIMEOUT_SECONDS)
        exchange = _Api(timeout=module.EXCHANGE_TIMEOUT_SECONDS)
        wallet = object()

        with (
            patch.object(module, "Info", return_value=info) as info_constructor,
            patch.object(module, "Exchange", return_value=exchange) as exchange_constructor,
            patch.object(module.eth_account.Account, "from_key", return_value=wallet),
        ):
            client = module.HLClient(
                secret_key="secret", account_address="0xaccount", mainnet=True,
            )

        info_constructor.assert_called_once_with(
            module.constants.MAINNET_API_URL,
            skip_ws=True,
            timeout=module.INFO_TIMEOUT_SECONDS,
        )
        exchange_constructor.assert_called_once_with(
            wallet,
            module.constants.MAINNET_API_URL,
            account_address="0xaccount",
            timeout=module.EXCHANGE_TIMEOUT_SECONDS,
        )
        self.assertIs(client.info, info)
        self.assertIs(client.exchange, exchange)

    def test_replaces_sdk_timeout_none_on_api_and_request(self):
        api = _Api(timeout=None)

        module._force_timeout(api, seconds=10)

        self.assertEqual(api.timeout, 10)
        self.assertEqual(api.session.request("POST", "/info", timeout=None), 10)

    def test_preserves_explicit_shorter_timeout(self):
        api = _Api(timeout=3)

        module._force_timeout(api, seconds=10)

        self.assertEqual(api.timeout, 3)
        self.assertEqual(api.session.request("POST", "/info", timeout=2), 2)


if __name__ == "__main__":
    unittest.main()
