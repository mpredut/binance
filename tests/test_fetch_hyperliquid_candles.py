import unittest

from offline.runners.fetch_hyperliquid_candles import (
    normalize_closed_candles,
    resolve_spot_pair,
    validate_continuity,
)


class HyperliquidDatasetTest(unittest.TestCase):
    def test_spot_pair_is_resolved_from_metadata_not_hardcoded(self):
        meta = {
            "tokens": [
                {"name": "USDC", "index": 0},
                {"name": "HYPE", "index": 150},
            ],
            "universe": [{"name": "@107", "tokens": [150, 0]}],
        }
        self.assertEqual(resolve_spot_pair("hype", "usdc", meta), "@107")

    def test_normalization_deduplicates_and_drops_open_candle(self):
        rows = [
            {"t": 2_000, "T": 2_999, "o": "2", "h": "4", "l": "1", "c": "3"},
            {"t": 1_000, "T": 1_999, "o": "1", "h": "3", "l": "0.5", "c": "2"},
            {"t": 2_000, "T": 2_999, "o": "2", "h": "5", "l": "1", "c": "4"},
            {"t": 3_000, "T": 3_999, "o": "4", "h": "6", "l": "3", "c": "5"},
        ]
        records = normalize_closed_candles(rows, now_ms=3_500)
        self.assertEqual([row["timestamp"] for row in records], [1, 2])
        self.assertEqual(records[-1]["high"], 5.0)
        self.assertEqual(records[-1]["close"], 4.0)

    def test_continuity_rejects_missing_bars(self):
        records = [{"timestamp": 0}, {"timestamp": 3_600}, {"timestamp": 10_800}]
        with self.assertRaisesRegex(ValueError, "dataset discontinuu"):
            validate_continuity(records, 60)


if __name__ == "__main__":
    unittest.main()
