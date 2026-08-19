"""Teste pentru offline/research/backtest_ranges.py — parsarea rangurilor de
test scrise ca text simplu deasupra unui parametru, in orice fisier de config."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from offline.research.backtest_ranges import scan_backtest_ranges

_TMP = "/tmp/claude_test_backtest_ranges.conf"


class TestScanBacktestRanges(unittest.TestCase):

    def _write(self, content):
        with open(_TMP, "w", encoding="utf-8") as f:
            f.write(content)
        self.addCleanup(lambda: os.path.exists(_TMP) and os.remove(_TMP))

    def test_supported_assignment_styles(self):
        cases = (
            (
                "ini",
                "# BACKTEST: 5.0, 6.0, 7.0, 8.0, 9.0\nmt.gain = 7.0\n",
                {"mt.gain": ["5.0", "6.0", "7.0", "8.0", "9.0"]},
            ),
            (
                "env",
                "# BACKTEST: 3, 4, 5.1, 6.5, 8\nTRADEALL_SLOPE_EXTREME_THRESHOLD=5.1\n",
                {"TRADEALL_SLOPE_EXTREME_THRESHOLD": ["3", "4", "5.1", "6.5", "8"]},
            ),
        )
        for label, content, expected in cases:
            with self.subTest(style=label):
                self._write(content)
                self.assertEqual(scan_backtest_ranges(_TMP), expected)

    def test_intervening_line_breaks_association(self):
        for label, separator in (("blank", "\n"), ("comment", "# alt comentariu explicativ\n")):
            with self.subTest(separator=label):
                self._write("# BACKTEST: 1, 2, 3\n" + separator + "mt.gain = 7.0\n")
                self.assertEqual(scan_backtest_ranges(_TMP), {})

    def test_multiple_annotations_in_same_file(self):
        self._write(
            "# BACKTEST: 5.0, 7.0, 9.0\n"
            "mt.gain = 7.0\n"
            "mt.lost = 3.3\n"
            "# BACKTEST: 2.3, 3.3, 4.3\n"
            "mt.lost2 = 3.3\n"
        )
        result = scan_backtest_ranges(_TMP)
        self.assertEqual(result, {"mt.gain": ["5.0", "7.0", "9.0"], "mt.lost2": ["2.3", "3.3", "4.3"]})

    def test_ini_sections_disambiguate_same_key_name(self):
        """instruments.conf refoloseste mt.gain in fiecare sectiune [NUME] —
        fara prefixare, a doua sectiune ar suprascrie tacut prima (bug real
        gasit azi pe fisierul REAL)."""
        self._write(
            "[BINANCE_BTC]\n"
            "# BACKTEST: 5.0, 7.0, 8.0, 9.0\n"
            "mt.gain = 7.0\n"
            "\n"
            "[BINANCE_TAO]\n"
            "# BACKTEST: 7.5, 9.2, 10.5, 12.0\n"
            "mt.gain = 9.2\n"
        )
        result = scan_backtest_ranges(_TMP)
        self.assertEqual(result, {
            "BINANCE_BTC.mt.gain": ["5.0", "7.0", "8.0", "9.0"],
            "BINANCE_TAO.mt.gain": ["7.5", "9.2", "10.5", "12.0"],
        })

    def test_file_without_sections_keeps_bare_key(self):
        self._write(
            "# BACKTEST: 3, 4, 5\n"
            "SOME_KEY=4\n"
        )
        self.assertEqual(scan_backtest_ranges(_TMP), {"SOME_KEY": ["3", "4", "5"]})

    def test_missing_or_unannotated_returns_empty(self):
        cases = (
            ("missing", "/tmp/does_not_exist_xyz123.conf", None),
            ("unannotated", _TMP, "mt.gain = 7.0\nmt.lost = 3.3\n"),
        )
        for label, path, content in cases:
            with self.subTest(case=label):
                if content is not None:
                    self._write(content)
                self.assertEqual(scan_backtest_ranges(path), {})


if __name__ == "__main__":
    unittest.main()
