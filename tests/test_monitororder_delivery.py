"""Delivery ownership regressions for monitororder replacements."""

from unittest.mock import patch

import monitororder


def test_failed_replacement_is_submitted_once_and_left_to_shared_outbox():
    failed_legacy = [{
        "symbol": "TAOUSDC", "order_type": "SELL",
        "price": 999.0, "quantity": 7.0,
    }]
    monitororder.initial_sell_prices.clear()
    open_orders = {"old-1": {"price": 100.0, "quantity": 2.0}}

    with (
        patch.object(monitororder.api, "get_open_orders", return_value=open_orders),
        patch.object(monitororder.api, "get_current_price", return_value=100.1),
        patch.object(monitororder.api, "cancel_order", return_value=True),
        patch.object(monitororder.mkt, "place", return_value=None) as place,
        patch.object(monitororder.time, "sleep") as sleep,
    ):
        monitororder.monitor_open_orders_by_type(
            "TAOUSDC", "SELL", failed_legacy,
        )

    place.assert_called_once()
    assert place.call_args.args[:2] == ("TAOUSDC", "SELL")
    assert place.call_args.kwargs["smart"] is False
    assert failed_legacy == [{
        "symbol": "TAOUSDC", "order_type": "SELL",
        "price": 999.0, "quantity": 7.0,
    }]
    sleep.assert_not_called()


def test_accepted_replacement_transfers_original_anchor_to_new_order_id():
    monitororder.initial_buy_prices.clear()
    open_orders = {"old-2": {"price": 100.0, "quantity": 1.5}}

    with (
        patch.object(monitororder.api, "get_open_orders", return_value=open_orders),
        patch.object(monitororder.api, "get_current_price", return_value=100.1),
        patch.object(monitororder.api, "cancel_order", return_value=True),
        patch.object(
            monitororder.mkt, "place",
            return_value={"orderId": "new-2"},
        ),
    ):
        monitororder.monitor_open_orders_by_type("TAOUSDC", "BUY")

    assert "old-2" not in monitororder.initial_buy_prices
    assert monitororder.initial_buy_prices["new-2"] == 100.0
