import os
import tempfile
import unittest

from rtrade_pair_store import RTradePairStore


class RTradePairStoreTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
