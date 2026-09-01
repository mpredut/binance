import pytest

from providers.strategy_executor import PairPrecision, ProviderError, candle_interval


@pytest.mark.parametrize(
    "kwargs",
    [
        {"price_decimals": -1, "volume_decimals": 2, "order_min": 0.01},
        {"price_decimals": 2, "volume_decimals": -1, "order_min": 0.01},
        {"price_decimals": 1.5, "volume_decimals": 2, "order_min": 0.01},
        {"price_decimals": 2, "volume_decimals": 2, "order_min": -0.01},
        {"price_decimals": 2, "volume_decimals": 2, "order_min": float("nan")},
    ],
)
def test_pair_precision_rejects_invalid_venue_metadata(kwargs):
    with pytest.raises(ValueError):
        PairPrecision(**kwargs)


def test_pair_precision_normalizes_valid_metadata_once():
    assert PairPrecision(2.0, 6, "0.01", " HYPE ") == PairPrecision(
        2, 6, 0.01, "HYPE"
    )


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(1, "1m"), (5, "5m"), (15, "15m"), (60, "1h"), (240, "4h"), (1440, "1d")],
)
def test_candle_interval_maps_only_supported_horizons(minutes, expected):
    assert candle_interval(minutes) == expected


@pytest.mark.parametrize("minutes", [None, "bad", 0, 30, 60.5])
def test_candle_interval_rejects_unknown_horizons(minutes):
    with pytest.raises(ProviderError):
        candle_interval(minutes)
