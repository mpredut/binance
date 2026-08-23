"""Caracterizare pentru API-urile legacy de plasare Binance.

Aceste functii raman compatibile pentru cod extern/arhivat, dar trebuie sa intre
prin acelasi pipeline guardat ca apelantii moderni ai ``MarketApi.place``.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from binance_api import bapi_placeorder as po


SYMBOL = "TAOUSDC"


def test_place_safe_order_delegates_to_common_pipeline_without_smart_repricing():
    expected = {"orderId": 101}
    with patch.object(po, "_guarded_market_place", return_value=expected) as place:
        result = po.place_safe_order(
            "buy", SYMBOL, 225.0, 2.0,
            safeback_seconds=123,
            force=True,
            cancelorders=True,
            hours=0.5,
            bypass_profit_guard=True,
        )

    assert result == expected
    place.assert_called_once_with(
        SYMBOL, "BUY", 225.0, 2.0,
        smart=False,
        safeback_seconds=123,
        force=True,
        cancelorders=True,
        hours=0.5,
        bypass_profit_guard=True,
    )


def test_place_order_smart_delegates_to_common_pipeline_with_motivation():
    expected = {"orderId": 202}
    with patch.object(po, "_guarded_market_place", return_value=expected) as place:
        result = po.place_order_smart(
            "sell", SYMBOL, 230.0, 1.5,
            safeback_seconds=456,
            force=False,
            cancelorders=False,
            hours=1.25,
            pair=True,
            motivation="legacy-test",
        )

    assert result == expected
    place.assert_called_once_with(
        SYMBOL, "SELL", 230.0, 1.5,
        smart=True,
        safeback_seconds=456,
        force=False,
        cancelorders=False,
        hours=1.25,
        motivation="legacy-test",
    )


def test_legacy_adapter_resolves_none_quantity_before_delegating():
    with (patch.object(po, "_resolve_qty", return_value=3.25) as resolve,
          patch.object(po, "_guarded_market_place", return_value=None) as place):
        po.place_safe_order("BUY", SYMBOL, 225.0, None)

    resolve.assert_called_once_with(None)
    assert place.call_args.args[:4] == (SYMBOL, "BUY", 225.0, 3.25)
