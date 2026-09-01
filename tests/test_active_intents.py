import json
import os

from active_intents import build_active_intent_index


def _write(path, payload, *, lines=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("\n".join(json.dumps(row) for row in payload) + "\n"
            if lines else json.dumps(payload))
    path.write_text(text, encoding="utf-8")


def test_normalizes_multiple_owners_without_writing(tmp_path):
    outbox = tmp_path / "cachedb/order_retry_queue.jsonl"
    t212 = tmp_path / "212trading/.state_NVDA_US_EQ.json"
    rtrade = tmp_path / "cachedb/rtrade_pairs.json"
    _write(outbox, [{
        "intent_id": "out-1", "provider_name": "Binance", "symbol": "TAOUSDC",
        "side": "BUY", "qty": 2, "lifecycle": "submit_pending",
    }], lines=True)
    _write(t212, {
        "pending_submit": {"intent_id": "t-1", "side": "SELL", "qty": 1,
                           "limit": 150, "submission_outcome": "unknown"},
    })
    _write(rtrade, {"pairs": {"pair-1": {
        "symbol": "BTCUSDC", "terminal": False,
        "intents": {"limit:BUY": {
            "intent_id": "r-1", "side": "BUY", "requested_qty": 0.1,
            "client_order_id": "CID-R", "order_id": "77",
        }},
    }}})
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in (outbox, t212, rtrade)}

    result = build_active_intent_index(str(tmp_path))

    assert result["read_only"] is True
    assert {row["intent_id"] for row in result["intents"]} == {"out-1", "t-1", "r-1"}
    assert next(row for row in result["intents"] if row["intent_id"] == "t-1")["status"] == "unknown"
    assert next(row for row in result["intents"] if row["intent_id"] == "r-1")["symbol"] == "BTCUSDC"
    for path, snapshot in before.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == snapshot


def test_excludes_terminal_intents_and_reports_corrupt_sources(tmp_path):
    _write(tmp_path / "cachedb/rtrade_pairs.json", {"pairs": {
        "done": {"terminal": True, "intents": {"x": {
            "intent_id": "done", "side": "SELL", "qty": 1,
        }}},
    }})
    corrupt = tmp_path / "cachedb/assetguardian_state.json"
    corrupt.write_text("{broken", encoding="utf-8")

    result = build_active_intent_index(str(tmp_path))

    assert result["intents"] == []
    assert result["errors"][0]["source"] == "cachedb/assetguardian_state.json"
