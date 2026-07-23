"""Teste pentru research/backtest_ranges.py (23 iul) — parsarea rangurilor de
test scrise ca text simplu deasupra unui parametru, in orice fisier de config."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))

from backtest_ranges import scan_backtest_ranges

_TMP = "/tmp/claude_test_backtest_ranges.conf"


class TestScanBacktestRanges(unittest.TestCase):

    def _write(self, content):
        with open(_TMP, "w", encoding="utf-8") as f:
            f.write(content)
        self.addCleanup(lambda: os.path.exists(_TMP) and os.remove(_TMP))

    def test_simple_annotation_ini_style(self):
        self._write(
            "# BACKTEST: 5.0, 6.0, 7.0, 8.0, 9.0\n"
            "mt.gain              = 7.0\n"
        )
        self.assertEqual(scan_backtest_ranges(_TMP),
                          {"mt.gain": ["5.0", "6.0", "7.0", "8.0", "9.0"]})

    def test_env_style_no_spaces(self):
        self._write(
            "# BACKTEST: 3, 4, 5.1, 6.5, 8\n"
            "TRADEALL_SLOPE_EXTREME_THRESHOLD=5.1\n"
        )
        self.assertEqual(scan_backtest_ranges(_TMP),
                          {"TRADEALL_SLOPE_EXTREME_THRESHOLD": ["3", "4", "5.1", "6.5", "8"]})

    def test_blank_line_between_breaks_association(self):
        self._write(
            "# BACKTEST: 1, 2, 3\n"
            "\n"
            "mt.gain = 7.0\n"
        )
        self.assertEqual(scan_backtest_ranges(_TMP), {})

    def test_other_comment_between_breaks_association(self):
        self._write(
            "# BACKTEST: 1, 2, 3\n"
            "# alt comentariu explicativ\n"
            "mt.gain = 7.0\n"
        )
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

    def test_missing_file_returns_empty(self):
        self.assertEqual(scan_backtest_ranges("/tmp/does_not_exist_xyz123.conf"), {})

    def test_no_annotations_returns_empty(self):
        self._write("mt.gain = 7.0\nmt.lost = 3.3\n")
        self.assertEqual(scan_backtest_ranges(_TMP), {})


if __name__ == "__main__":
    unittest.main()
