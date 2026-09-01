import os
import tempfile
import unittest

from rtrade_pair_store import RTradePairStore, rtrade_client_order_id


class RTradePairStoreTest(unittest.TestCase):
    def test_the_client_order_id_is_stable_and_rtrade_specific(self):
        first = rtrade_client_order_id("pair-1", "buy")
        self.assertEqual(first, rtrade_client_order_id("pair-1", "BUY"))
        self.assertTrue(first.startswith("RT_"))
        self.assertEqual(len(first), 35)
        self.assertNotEqual(first, rtrade_client_order_id("pair-1", "SELL"))
        self.assertNotEqual(
            first, rtrade_client_order_id("pair-1", "BUY", "hard_stop"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RTradePairStore(os.path.join(self.tmp.name, "pairs.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_intent_is_durable_before_acceptance_and_checkpoint(self):
        self.store.begin("TAOUSDC", "pair-1", "BUY", 2.0)
        self.store.intent(
            "pair-1", "BUY", 100.0, 2.0, "SD_client", kind="limit")
        pending = self.store.active("TAOUSDC")[0]
        self.assertIsNone(pending["intents"]["limit:BUY"]["order_id"])

        self.store.accepted("pair-1", "BUY", "77", kind="limit")
        state = {"pair_id": "pair-1", "phase": "quoting", "tickets": []}
        self.store.checkpoint("pair-1", state, terminal=False)
        adopted = self.store.active("TAOUSDC")[0]
        self.assertEqual(adopted["intents"]["limit:BUY"]["order_id"], "77")
        self.assertEqual(adopted["state"]["phase"], "quoting")

    def test_terminal_pair_is_not_recovered(self):
        self.store.begin("TAOUSDC", "pair-2", "SELL", 1.0)
        self.store.checkpoint(
            "pair-2", {"pair_id": "pair-2", "phase": "complete", "tickets": []},
            terminal=True)
        self.assertEqual(self.store.active("TAOUSDC"), [])

    def test_intent_can_atomically_create_pair_without_separate_begin(self):
        self.store.intent(
            "pair-3", "BUY", 100.0, 1.0, "SD_atomic", kind="limit",
            symbol="TAOUSDC", start_side="BUY")
        rec = self.store.active("TAOUSDC")[0]
        self.assertEqual(rec["pair_id"], "pair-3")
        self.assertEqual(rec["intents"]["limit:BUY"]["client_order_id"],
                         "SD_atomic")

    def test_canonical_tracked_intent_replaces_and_removes_exact_leg(self):
        pending = {
            "intent_id": "rtrade:pair-4:limit:buy",
            "client_order_id": "RT_canonical",
            "symbol": "TAOUSDC", "side": "BUY", "kind": "limit",
            "requested_qty": 1.25, "requested_price": 99.5,
            "attempt": 1, "created_at": 100.0, "lookup_misses": 0,
        }
        self.store.persist_intent(
            "pair-4", "BUY", "limit", pending,
            symbol="TAOUSDC", start_side="BUY")
        rec = self.store.active("TAOUSDC")[0]
        stored = rec["intents"]["limit:BUY"]
        self.assertEqual(stored["requested_qty"], 1.25)
        self.assertEqual(stored["requested_price"], 99.5)
        self.assertEqual(stored["qty"], 1.25)
        self.assertEqual(stored["price"], 99.5)

        accepted = dict(pending, order_id="77", submitted_qty=1.2)
        self.store.persist_intent(
            "pair-4", "BUY", "limit", accepted, symbol="TAOUSDC")
        rec = self.store.active("TAOUSDC")[0]
        self.assertEqual(rec["intents"]["limit:BUY"]["order_id"], "77")

        self.store.persist_intent(
            "pair-4", "BUY", "limit", None, symbol="TAOUSDC")
        rec = self.store.active("TAOUSDC")[0]
        self.assertNotIn("limit:BUY", rec["intents"])

    def test_checkpoint_many_updates_rounds_in_one_transaction(self):
        for pair_id in ("p1", "p2"):
            self.store.begin("TAOUSDC", pair_id, "BUY", 1.0)
        self.store.checkpoint_many([
            ("p1", {"phase": "quoting"}, False),
            ("p2", {"phase": "complete"}, True),
        ])
        active = self.store.active("TAOUSDC")
        self.assertEqual([rec["pair_id"] for rec in active], ["p1"])

    def test_terminal_history_is_bounded(self):
        store = RTradePairStore(
            os.path.join(self.tmp.name, "bounded.json"), terminal_retention=2)
        for i in range(4):
            pair_id = f"t{i}"
            store.begin("TAOUSDC", pair_id, "BUY", 1.0)
            store.checkpoint(pair_id, {"phase": "complete"}, terminal=True)
        import json
        with open(store.path, encoding="utf-8") as handle:
            pairs = json.load(handle)["pairs"]
        self.assertEqual(len(pairs), 2)


if __name__ == "__main__":
    unittest.main()
