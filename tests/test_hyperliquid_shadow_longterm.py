from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
HL_DIR = ROOT / "hyperliquid"
KRAKEN_DIR = ROOT / "kraken"
for path in (str(ROOT), str(HL_DIR), str(KRAKEN_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

SPEC = importlib.util.spec_from_file_location(
    "hl_shadow_longterm_under_test", HL_DIR / "shadow_longterm.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class HyperliquidLongtermShadowTest(unittest.TestCase):
    def test_variants_are_fixed_and_paper_only_parameters(self):
        variants = module._variants(240)
        self.assertEqual(list(variants), ["current", "long_tp3_trail3", "reentry4"])
        candidate = variants["long_tp3_trail3"]
        self.assertEqual(candidate.takeprofit_pct, 3.0)
        self.assertTrue(candidate.tp_trend_hold)
        self.assertFalse(candidate.tp_trail_adaptive)
        self.assertEqual(candidate.tp_trail_pct, 3.0)
        self.assertEqual(variants["reentry4"].reentry_drop_pct, 4.0)

    def test_rejects_non_native_interval(self):
        with self.assertRaisesRegex(ValueError, "240m"):
            module._variants(60)

    def test_public_fetch_constructs_client_without_secret_and_drops_open_bar(self):
        fake = MagicMock()
        fake.candles.return_value = [
            {"t": 1000, "o": "1", "h": "2", "l": "0.5", "c": "1.5"},
            {"t": 2000, "o": "1.5", "h": "3", "l": "1", "c": "2.5"},
        ]
        with patch("hl_client.HLClient", return_value=fake) as constructor:
            rows = module._fetch_with_ts("ignored", 240)
        constructor.assert_called_once_with()
        self.assertEqual(rows, [(1, 1.0, 2.0, 0.5, 1.5)])

    def test_snapshot_injects_isolated_log_dir_and_public_fetch(self):
        with patch.object(module.shadow, "snapshot", return_value={"ok": True}) as call:
            result = module.snapshot(quiet=True)
        self.assertEqual(result, {"ok": True})
        call.assert_called_once_with("HYPE-HL", 240, 0.07, quiet=True)
        self.assertEqual(module.shadow.LOG_DIR, module.LOG_DIR)


if __name__ == "__main__":
    unittest.main()
