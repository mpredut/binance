"""Agregări pure pentru calibrarea modelului de execuție din auditul real."""

from __future__ import annotations

import statistics


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def distribution(values: list[float]) -> dict:
    values = [float(value) for value in values]
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
        "p50": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "p95": _percentile(values, 95),
        "max": max(values) if values else None,
    }


def calibrate_execution_events(events: list[dict]) -> dict:
    """Corelează evenimentele pe ``intent_id`` fără a presupune fill-uri lipsă."""
    intents: dict[str, list[dict]] = {}
    for event in sorted(events, key=lambda item: float(item.get("ts") or 0.0)):
        intent_id = str(event.get("intent_id") or "").strip()
        if intent_id:
            intents.setdefault(intent_id, []).append(event)

    orders = []
    for intent_id, history in intents.items():
        requested = next(
            (event for event in history if event.get("event") == "submit_requested"),
            None,
        )
        if requested is None:
            continue
        accepted = next(
            (event for event in history if event.get("event") == "submit_accepted"),
            None,
        )
        statuses = [event for event in history if event.get("event") == "order_status"]
        qty = float(requested.get("qty") or 0.0)
        status_rows = []
        for status in statuses:
            filled = float(status.get("filled_qty") or 0.0)
            cost = float(status.get("cost") or 0.0)
            fee = float(status.get("fee") or 0.0)
            status_rows.append((status, filled, cost, fee))
        latest = max(
            status_rows,
            key=lambda row: (row[1], float(row[0].get("ts") or 0.0)),
            default=None,
        )
        first_fill = next((row for row in status_rows if row[1] > 0), None)
        filled = latest[1] if latest else 0.0
        cost = latest[2] if latest else 0.0
        fee = latest[3] if latest else 0.0
        fill_ratios = [
            min(1.0, max(0.0, row[1] / qty))
            for row in status_rows if qty > 0 and row[1] > 0
        ]
        avg_fill = cost / filled if filled > 0 and cost > 0 else None
        requested_price = requested.get("price")
        deviation_bps = None
        if requested_price is not None and avg_fill is not None:
            reference = float(requested_price)
            if reference > 0:
                side = str(requested.get("side") or "").lower()
                direction = 1.0 if side == "buy" else -1.0
                deviation_bps = direction * (avg_fill / reference - 1.0) * 10_000
        latency = None
        if accepted is not None and first_fill is not None:
            latency = max(
                0.0,
                float(first_fill[0].get("ts") or 0.0)
                - float(accepted.get("ts") or 0.0),
            )
        orders.append({
            "intent_id": intent_id,
            "venue": requested.get("venue"),
            "symbol": requested.get("symbol"),
            "market": bool(requested.get("market")),
            "accepted": accepted is not None,
            "rejected": any(
                event.get("event") == "submit_rejected" for event in history
            ),
            "filled_qty": filled,
            "requested_qty": qty,
            "final_fill_ratio": min(1.0, filled / qty) if qty > 0 else None,
            "ever_partial": any(0.0 < ratio < 1.0 - 1e-9 for ratio in fill_ratios),
            "fee_bps": abs(fee) / cost * 10_000 if cost > 0 else None,
            "first_fill_latency_s": latency,
            "limit_fill_deviation_bps": deviation_bps,
        })

    def summarize(selected: list[dict]) -> dict:
        filled = [order for order in selected if order["filled_qty"] > 0]
        return {
            "orders": len(selected),
            "accepted": sum(order["accepted"] for order in selected),
            "rejected": sum(order["rejected"] for order in selected),
            "filled": len(filled),
            "ever_partial": sum(order["ever_partial"] for order in filled),
            "fee_bps": distribution([
                order["fee_bps"] for order in filled
                if order["fee_bps"] is not None
            ]),
            "first_fill_latency_s": distribution([
                order["first_fill_latency_s"] for order in filled
                if order["first_fill_latency_s"] is not None
            ]),
            "final_fill_ratio": distribution([
                order["final_fill_ratio"] for order in filled
                if order["final_fill_ratio"] is not None
            ]),
            "limit_fill_deviation_bps": distribution([
                order["limit_fill_deviation_bps"] for order in filled
                if order["limit_fill_deviation_bps"] is not None
            ]),
        }

    market = [order for order in orders if order["market"]]
    limit = [order for order in orders if not order["market"]]
    filled_count = sum(order["filled_qty"] > 0 for order in orders)
    return {
        "orders": orders,
        "summary": {
            "all": summarize(orders),
            "market": summarize(market),
            "limit": summarize(limit),
        },
        "calibration_readiness": {
            "minimum_filled_orders": 20,
            "filled_orders": filled_count,
            "enough_total_fills": filled_count >= 20,
            "has_market_fee_samples": any(
                order["market"] and order["fee_bps"] is not None for order in orders
            ),
            "has_limit_fee_samples": any(
                not order["market"] and order["fee_bps"] is not None
                for order in orders
            ),
            "can_calibrate_market_slippage": False,
            "market_slippage_blocker": (
                "submit_requested nu conține quote/mid de referință pentru ordine MARKET"
            ),
            "can_calibrate_spread": False,
            "spread_blocker": "auditul nu conține bid/ask la momentul deciziei",
        },
    }
