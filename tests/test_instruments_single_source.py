"""instruments.conf is the SINGLE registry for the Binance fleet.

symbols.py (the Binance symbol list), binance_api/trailing_stop.py (per-coin trail
%) and tradeall.py (the order allowlist) all DERIVE from it via the helpers in
instruments_config. This suite is the golden safety net for that refactor:

* it locks the derived values to the pre-refactor hardcoded behaviour, so the
  single-source refactor provably changed nothing about which coins are traded,
  trailed or trend-tracked;
* it guards the import-lightness that lets symbols.py (imported fleet-wide) read
  the registry without dragging in the provider stack or risking an import cycle.

When a coin is added/retuned in instruments.conf, update the expected values here
deliberately — a diff in this test is the intended signal that fleet coverage moved.
"""
import os
import subprocess
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import instruments_config as ic  # noqa: E402


class TestInstrumentsSingleSource(unittest.TestCase):

    def test_binance_symbols_match_pre_refactor(self):
        # Registry order matters: several call sites index/print the list.
        self.assertEqual(ic.binance_symbols(), ["BTCUSDC", "TAOUSDC", "ARBUSDC"])

    def test_trail_pct_map_matches_pre_refactor(self):
        self.assertEqual(
            ic.trail_pct_map(),
            {"BTCUSDC": 20.0, "TAOUSDC": 22.0, "ARBUSDC": 26.0},
        )

    def test_tradeall_allowlist_matches_pre_refactor(self):
        self.assertEqual(ic.tradeall_trade_symbols(), {"BTCUSDC", "TAOUSDC"})

    def test_arb_is_trend_tracked_but_not_traded(self):
        # The whole point of the manual ARB position: observed + trailed, never
        # traded by tradeall.
        self.assertIn("ARBUSDC", ic.binance_symbols())
        self.assertIn("ARBUSDC", ic.trail_pct_map())
        self.assertNotIn("ARBUSDC", ic.tradeall_trade_symbols())

    def test_trail_and_trade_are_subsets_of_symbols(self):
        syms = set(ic.binance_symbols())
        self.assertLessEqual(set(ic.trail_pct_map()), syms)
        self.assertLessEqual(ic.tradeall_trade_symbols(), syms)

    def test_missing_registry_fails_loud(self):
        with self.assertRaises(FileNotFoundError):
            ic.binance_symbols(path="/nonexistent/instruments.conf")

    def test_registry_import_is_light(self):
        # symbols.py imports instruments_config at module load, fleet-wide. It must
        # NOT pull in providers.market_api (heavy chain + cycle risk). Checked in a
        # fresh interpreter so an unrelated test that imported providers cannot mask it.
        code = (
            "import sys, instruments_config; "
            "assert 'providers.market_api' not in sys.modules, "
            "'instruments_config dragged in the provider stack'; "
            "print('ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=_ROOT,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
