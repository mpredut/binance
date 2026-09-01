#!/usr/bin/env python3
"""Test SPCX launch detection with stale metadata but a live intraday series.

The HTTP getter is patched, so the test performs no network access.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_data as md  # noqa: E402


def chart(price=None, meta_vol=0, meta_age_s=None, state="REGULAR", series=None):
    """series = [(age_secunde, close, volume), ...] cele mai recente bare."""
    meta = {"marketState": state, "currency": "USD"}
    if price is not None:
        meta["regularMarketPrice"] = price
    meta["regularMarketVolume"] = meta_vol
    if meta_age_s is not None:
        meta["regularMarketTime"] = time.time() - meta_age_s
    ts, closes, vols = [], [], []
    for age_s, c, v in (series or []):
        ts.append(int(time.time() - age_s)); closes.append(c); vols.append(v)
    return json.dumps({"chart": {"result": [{
        "meta": meta, "timestamp": ts,
        "indicators": {"quote": [{"close": closes, "volume": vols}]}}]}})


class Base(unittest.TestCase):
    def setUp(self):
        self._orig = md.http_get
    def tearDown(self):
        md.http_get = self._orig
    def feed(self, body, status=200):
        md.http_get = lambda url, headers=None: (status, body)


class TestLansare(Base):
    def test_placeholder_pre_ipo_NU_e_lansat(self):
        # stale meta (vol 0, old) + no series -> placeholder, not launched
        self.feed(chart(price=135.0, meta_vol=0, meta_age_s=55 * 3600, series=[]))
        m = md.check_market("SPCX")
        self.assertFalse(m["launched"])

    def test_placeholder_serie_plata_NU_e_lansat(self):
        # a series with the same price repeated (no movement, no volume) -> not launched
        self.feed(chart(price=135.0, meta_vol=0, meta_age_s=55 * 3600,
                        series=[(600, 135.0, 0), (60, 135.0, 0)]))
        self.assertFalse(md.check_market("SPCX")["launched"])

    def test_CAZUL_SPCX_meta_statuta_dar_serie_live(self):
        # META says vol=0/old price 135 (the bug), but the SERIES has live bars at 164-166
        self.feed(chart(price=135.0, meta_vol=0, meta_age_s=55 * 3600,
                        series=[(600, 164.0, 100), (300, 165.0, 200), (60, 166.0, 150)]))
        m = md.check_market("SPCX")
        self.assertTrue(m["launched"], "seria live trebuie sa declare lansarea")
        self.assertEqual(m["price"], 166.0, "the price is the last live bar, not the 135 from meta")

    def test_miscare_de_pret_fara_volum_e_lansat(self):
        # Some feeds omit volume; a fresh moving price still proves that it trades.
        self.feed(chart(price=135.0, meta_vol=0, meta_age_s=55 * 3600,
                        series=[(300, 164.0, None), (60, 167.0, None)]))
        self.assertTrue(md.check_market("SPCX")["launched"])

    def test_actiune_listata_normal_NVDA(self):
        self.feed(chart(price=204.0, meta_vol=1_000_000, meta_age_s=300,
                        series=[(300, 204.0, 5000)]))
        self.assertTrue(md.check_market("NVDA")["launched"])

    def test_serie_VECHE_nu_declanseaza(self):
        # a series with bars but all old (> 20 min) -> it does not confirm a launch now
        self.feed(chart(price=135.0, meta_vol=0, meta_age_s=55 * 3600,
                        series=[(3600, 164.0, 100), (1800, 165.0, 200)]))
        self.assertFalse(md.check_market("SPCX")["launched"])

    def test_fara_date_returneaza_none(self):
        self.feed(json.dumps({"chart": {"result": []}}))
        self.assertIsNone(md.check_market("SPCX"))


class TestPretProaspat(Base):
    def test_get_price_prefera_seria(self):
        self.feed(chart(price=135.0, meta_vol=0, meta_age_s=55 * 3600,
                        series=[(120, 164.0, 100), (30, 168.0, 200)]))
        self.assertEqual(md.get_price_usd("SPCX"), 168.0)

    def test_get_price_cade_pe_meta_fara_serie(self):
        self.feed(chart(price=204.0, meta_vol=1000, meta_age_s=60, series=[]))
        self.assertEqual(md.get_price_usd("NVDA"), 204.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
