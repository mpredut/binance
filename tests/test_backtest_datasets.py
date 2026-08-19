import json
import tempfile
import unittest
from pathlib import Path

from offline.backtests.datasets import (
    dataset_metadata,
    load_dataset,
    save_dataset,
    validate_dataset,
)


def _rows():
    return [
        {"timestamp": 0, "open": 10, "high": 12, "low": 9, "close": 11},
        {"timestamp": 3600, "open": 11, "high": 13, "low": 10, "close": 12},
    ]


class OhlcDatasetContractTest(unittest.TestCase):
    def test_round_trip_is_canonical_and_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.csv"
            save_dataset(_rows(), path)
            loaded = load_dataset(path)
            self.assertEqual(loaded, validate_dataset(_rows(), interval_minutes=60))
            metadata = dataset_metadata(loaded, interval_minutes=60)
            self.assertEqual(metadata["bars"], 2)
            self.assertEqual(len(metadata["sha256"]), 64)

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


if __name__ == "__main__":
    unittest.main()
