from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import cacheManager as cm
import tradeall_price_archiver as archiver


class _Current:
    def __init__(self, events):
        self.events = events

    def unsubscribe_price(self, cache):
        self.events.append(("unsubscribe", cache))

    def shutdown(self):
        self.events.append(("current_shutdown", None))


class _Cache:
    def __init__(self, events):
        self.events = events

    def shutdown(self):
        self.events.append(("cache_shutdown", self))

    def save_state_to_file(self):
        self.events.append(("flush", self))


class _WsManager:
    def __init__(self, events):
        self.events = events

    def stop(self):
        self.events.append(("ws_stop", None))


class _WsModule:
    def __init__(self, events):
        self.bapi_ws_manager = _WsManager(events)


class TestArchiverLifecycle(unittest.TestCase):
    def test_symbols_are_normalized_deduplicated_and_validated(self):
        self.assertEqual(archiver._symbols(" btcusdc,TAOUSDC,BTCUSDC "),
                         ["BTCUSDC", "TAOUSDC"])
        with self.assertRaises(ValueError):
            archiver._symbols("../escape")
        with self.assertRaises(ValueError):
            archiver._symbols("")

    def test_shutdown_unsubscribes_then_stops_and_flushes_before_ws_close(self):
        events = []
        cache = _Cache(events)
        archiver._shutdown([cache], _Current(events), _WsModule(events))
        self.assertEqual([name for name, _ in events], [
            "unsubscribe", "cache_shutdown", "flush", "current_shutdown", "ws_stop"])

    def test_jsonl_restart_loads_existing_history_and_appends_only_new_tick(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"s": "BTCUSDC", "i": [1000, 10.0]}) + "\n")
            manager = cm.Cache24LongPriceManager(60, ["BTCUSDC"], path)
            manager.KEEP_HOURS = 10**9
            manager.on_price_update("BTCUSDC", 2000, 11.0)
            manager.save_state_to_file()
            with open(path, encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(rows, [
                {"s": "BTCUSDC", "i": [1000, 10.0]},
                {"s": "BTCUSDC", "i": [2000, 11.0]},
            ])
        finally:
            for suffix in ("", ".meta"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass

    def test_long_archive_compaction_is_batched(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            manager = cm.Cache24LongPriceManager(60, ["BTCUSDC"], path)
            manager.KEEP_HOURS = 1
            manager.cache = {"BTCUSDC": [[1, 10.0]]}
            manager._last_long_trim = 100.0
            with mock.patch("cacheManager.time.monotonic", side_effect=[101.0, 100.0 + 86401]), \
                    mock.patch.object(manager, "compact_jsonl") as compact:
                manager._trim_old_data("BTCUSDC")
                compact.assert_not_called()
                manager._trim_old_data("BTCUSDC")
                compact.assert_called_once()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
