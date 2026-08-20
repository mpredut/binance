import json
import tempfile
import unittest
from pathlib import Path

from offline.backtests.datasets import (
    align_previous_values,
    dataset_metadata,
    drop_incomplete_last_bar,
    load_dataset,
    merge_datasets,
    save_dataset,
    validate_dataset,
)


def _rows():
    return [
        {"timestamp": 0, "open": 10, "high": 12, "low": 9, "close": 11},
        {"timestamp": 3600, "open": 11, "high": 13, "low": 10, "close": 12},
    ]


class OhlcDatasetContractTest(unittest.TestCase):
    def test_drops_only_the_incomplete_intraday_bar(self):
        rows = _rows()
        self.assertEqual(
            drop_incomplete_last_bar(
                rows, interval_minutes=60, now_timestamp=7199,
            ),
            rows[:1],
        )
        self.assertEqual(
            drop_incomplete_last_bar(
                rows, interval_minutes=60, now_timestamp=7200,
            ),
            rows,
        )

    def test_drops_multiple_incomplete_yahoo_tail_rows(self):
        rows = _rows() + [
            {"timestamp": 3900, "open": 12, "high": 13, "low": 11, "close": 12},
        ]
        self.assertEqual(
            drop_incomplete_last_bar(
                rows, interval_minutes=60, now_timestamp=3700,
            ),
            rows[:1],
        )

    def test_daily_bar_uses_regular_session_end(self):
        rows = [{"timestamp": 100, "open": 10, "high": 12, "low": 9, "close": 11}]
        self.assertEqual(
            drop_incomplete_last_bar(
                rows, interval_minutes=1440, now_timestamp=150,
                regular_session_start=100, regular_session_end=200,
            ),
            [],
        )
        self.assertEqual(
            drop_incomplete_last_bar(
                rows, interval_minutes=1440, now_timestamp=200,
                regular_session_start=100, regular_session_end=200,
            ),
            rows,
        )

    def test_asof_alignment_never_uses_a_future_fx_value(self):
        fx = [
            {"timestamp": 10, "close": 0.20},
            {"timestamp": 20, "close": 0.25},
        ]
        self.assertEqual(
            align_previous_values([10, 19, 20, 30], fx),
            [0.20, 0.20, 0.25, 0.25],
        )
        with self.assertRaisesRegex(ValueError, "extinde datasetul FX"):
            align_previous_values([9], fx)

    def test_round_trip_is_canonical_and_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            save_dataset(_rows(), path)
            loaded = load_dataset(path)
            self.assertEqual(loaded, validate_dataset(_rows(), interval_minutes=60))
            metadata = dataset_metadata(loaded, interval_minutes=60)
            self.assertEqual(metadata["bars"], 2)
            self.assertEqual(len(metadata["sha256"]), 64)

    def test_merge_overlapping_windows_preserves_history_and_newer_value(self):
        older = _rows()
        newer = [
            {**_rows()[1], "close": 12.5, "high": 13.5},
            {"timestamp": 7200, "open": 12.5, "high": 14, "low": 12, "close": 13},
        ]
        merged = merge_datasets(older, newer)

        self.assertEqual([row["timestamp"] for row in merged], [0, 3600, 7200])
        self.assertEqual(merged[1]["close"], 12.5)

    def test_rejects_gap_duplicate_and_invalid_ohlc(self):
        with self.assertRaisesRegex(ValueError, "cadență"):
            validate_dataset([_rows()[0], {**_rows()[1], "timestamp": 7200}], interval_minutes=60)
        with self.assertRaisesRegex(ValueError, "duplicat"):
            validate_dataset([_rows()[0], {**_rows()[1], "timestamp": 0}])
        with self.assertRaisesRegex(ValueError, "OHLC"):
            validate_dataset([{"timestamp": 0, "open": 10, "high": 9, "low": 8, "close": 10}])

    def test_versioned_hype_manifest_matches_frozen_files(self):
        root = Path(__file__).resolve().parents[1]
        directory = root / "offline/research/hype_dataset"
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        for interval, expected in manifest["datasets"].items():
            records = load_dataset(directory / expected["file"])
            actual = dataset_metadata(records, interval_minutes=int(interval))
            for key in ("sha256", "bars", "start_utc", "end_utc"):
                self.assertEqual(actual[key], expected[key])

    def test_versioned_t212_manifest_matches_frozen_files(self):
        root = Path(__file__).resolve().parents[1]
        directory = root / "offline/research/t212_dataset"
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        for expected in manifest["datasets"].values():
            records = load_dataset(directory / expected["file"])
            actual = dataset_metadata(records)
            for key in ("sha256", "bars", "start_utc", "end_utc"):
                self.assertEqual(actual[key], expected[key])


if __name__ == "__main__":
    unittest.main()
