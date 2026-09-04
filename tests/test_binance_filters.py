from decimal import Decimal

import pytest

from providers.binance_filters import (
    BinanceFilterError,
    BinanceOrderRules,
    binance_filter_refusal_reason,
    decimal_places,
)


def _info():
    return {"baseAsset": "TAO", "quoteAsset": "USDC", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.05", "minPrice": "0.05", "maxPrice": "10000"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.010", "maxQty": "100"},
        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.01", "minQty": "0.10", "maxQty": "50"},
        {"filterType": "NOTIONAL", "minNotional": "10", "maxNotional": "100000",
         "applyMinToMarket": True, "applyMaxToMarket": True},
    ]}


def test_limit_normalization_floors_to_exchange_steps():
    rules = BinanceOrderRules.from_symbol_info(_info())
    qty, price = rules.normalize(quantity="1.2349", price="101.079")
    assert qty == Decimal("1.234")
    assert price == Decimal("101.05")
    assert decimal_places(rules.tick_size) == 2
    assert decimal_places(rules.lot_step) == 3


def test_market_uses_market_lot_and_reference_notional():
    rules = BinanceOrderRules.from_symbol_info(_info())
    qty, price = rules.normalize(quantity="1.239", market=True, reference_price="100")
    assert qty == Decimal("1.23")
    assert price is None


def test_filter_refusal_reason_is_stable_for_retry_classification():
    assert binance_filter_refusal_reason(
        BinanceFilterError("notional 5 is below minimum 10")) == "below_min_notional"
    assert binance_filter_refusal_reason(
        BinanceFilterError("quantity 0 violates [1, unbounded]")) == (
            "binance_filter_refused:quantity 0 violates [1, unbounded]")
    assert binance_filter_refusal_reason(
        BinanceFilterError("invalid reference_price: None")) == (
            "binance_filter_unavailable:invalid reference_price: None")


def test_exchange_and_business_minima_are_distinct_and_fail_closed():
    rules = BinanceOrderRules.from_symbol_info(_info())
    with pytest.raises(BinanceFilterError, match="below minimum 100"):
        rules.normalize(quantity="0.5", price="100", business_min_notional="100")
    with pytest.raises(BinanceFilterError, match="PRICE_FILTER"):
        BinanceOrderRules.from_symbol_info({"baseAsset": "TAO", "quoteAsset": "USDC", "filters": []})
