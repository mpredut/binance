from pathlib import Path

from verify_tools.direct_order_api_inventory import inventory, scan_direct_calls


def test_production_direct_order_boundaries_match_reviewed_allowlist():
    report = inventory()
    assert report["unapproved"] == []
    assert report["stale_allowlist"] == []


def test_new_direct_submit_is_detected(tmp_path: Path):
    path = tmp_path / "new_strategy.py"
    path.write_text(
        "def trade(client):\n    return client.order_market_buy(symbol='BTCUSDC', quantity=1)\n",
        encoding="utf-8",
    )
    calls = scan_direct_calls(tmp_path)
    assert {(call.path, call.function, call.method) for call in calls} == {
        ("new_strategy.py", "trade", "order_market_buy"),
    }
