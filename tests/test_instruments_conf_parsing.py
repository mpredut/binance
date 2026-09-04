"""Regression coverage for the authoritative instrument registry.

The loader must preserve inline-comment parsing and reject any incomplete or
ambiguous core metadata before a trading consumer can start.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import instruments_config as ic


NUMERIC_MT_KEYS = [
    "gain", "lost", "maxage_days", "hardtp", "hardtp_fraction",
    "hardtp_cooldown_h", "buy_budget", "max_budget",
]


class _Provider:
    name = "fake"

    @staticmethod
    def validate_symbol(symbol):
        if not symbol:
            raise ValueError("empty symbol")


class _Api:
    @staticmethod
    def provider_by_name(name):
        return _Provider() if name.casefold() == "fake" else None


class TestInstrumentRegistry(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.registry = Path(self._temp_dir.name) / "instruments.conf"

    def tearDown(self):
        self._temp_dir.cleanup()

    def _write(self, content):
        self.registry.write_text(content, encoding="utf-8")
        return str(self.registry)

    def _valid_section(self, *, name="ONE", symbol="BTCUSD", extra=""):
        return f"""[{name}]
provider = fake
symbol = {symbol}
base = BTC
quote = USD
enabled = yes
isolation = own_ledger
market_hours = 24x7
{extra}
"""

    def test_missing_registry_fails_closed(self):
        missing = str(Path(self._temp_dir.name) / "missing.conf")
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            ic.load_instruments(missing, api=_Api())

    def test_missing_core_field_fails_closed(self):
        path = self._write(self._valid_section().replace('quote = USD\n', ""))
        with self.assertRaisesRegex(ValueError, "missing required 'quote'"):
            ic.load_instruments(path, api=_Api())

    def test_invalid_core_enums_fail_closed(self):
        cases = {
            "enabled": self._valid_section().replace("enabled = yes", "enabled = perhaps"),
            "isolation": self._valid_section().replace(
                "isolation = own_ledger", "isolation = shared"),
            "market_hours": self._valid_section().replace(
                "market_hours = 24x7", "market_hours = weekends"),
        }
        for name, content in cases.items():
            with self.subTest(field=name):
                path = self._write(content)
                with self.assertRaises(ValueError):
                    ic.load_instruments(path, api=_Api())

    def test_duplicate_provider_symbol_fails_closed(self):
        path = self._write(
            self._valid_section(name="ONE", symbol="BTCUSD")
            + self._valid_section(name="TWO", symbol="btcusd"))
        with self.assertRaisesRegex(ValueError, "duplicate provider/symbol"):
            ic.load_instruments(path, api=_Api())

    def test_inline_comments_and_false_boolean_parse_without_defaults(self):
        path = self._write(
            self._valid_section(extra='enabled = off # intentionally disabled\n')
            .replace('enabled = yes\n', ""))
        instrument = ic.load_instruments(path, api=_Api())["ONE"]
        self.assertFalse(instrument.enabled)
        self.assertEqual(instrument.isolation, "own_ledger")


class TestCurrentRegistry(unittest.TestCase):
    def test_all_enabled_mt_instruments_parse_numeric_params(self):
        instruments = ic.load_instruments()
        raw_sections = _raw_param_presence()
        for name, inst in instruments.items():
            if not inst.enabled:
                continue
            for key in NUMERIC_MT_KEYS:
                if f"mt.{key}" not in raw_sections.get(name, set()):
                    continue
                value = inst.param("mt", key, None, float)
                self.assertIsNotNone(
                    value,
                    f"[{name}] mt.{key} is present but cannot be parsed as a float")

    def test_budget_regressions(self):
        instruments = ic.load_instruments()
        cases = {
            "BINANCE_BTC": (250.0, 3500.0),
            "BINANCE_TAO": (250.0, 3500.0),
            "KRAKEN_HYPE": (200.0, 5000.0),
        }
        for name, (buy_budget, max_budget) in cases.items():
            with self.subTest(instrument=name):
                inst = instruments[name]
                self.assertEqual(inst.param("mt", "buy_budget", None, float), buy_budget)
                self.assertEqual(inst.param("mt", "max_budget", None, float), max_budget)


def _raw_param_presence():
    """Read the registry independently to identify declared mt parameters."""
    import configparser
    path = Path(__file__).resolve().parents[1] / "instruments.conf"
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cp.read(path)
    return {
        section: {key for key in cp[section] if key.startswith("mt.")}
        for section in cp.sections()
    }


if __name__ == "__main__":
    unittest.main()
