#!/usr/bin/env python3
"""Freeze public Hyperliquid candles into a CSV compatible with the replay.

The HYPE/USDC spot data is a cross-venue proxy for validating the robustness of
the Kraken strategy. It does not model Kraken spread, liquidity or fills.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from offline.backtests.datasets import canonical_bytes, dataset_sha256


API_URL = "https://api.hyperliquid.xyz/info"
INTERVALS = {
    1: "1m",
    3: "3m",
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
    120: "2h",
    240: "4h",
    480: "8h",
    720: "12h",
    1440: "1d",
    4320: "3d",
    10080: "1w",
}


def _post(body: dict) -> object:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "binance-repo/hyperliquid-research-dataset",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read())


def resolve_spot_pair(token: str, quote: str, spot_meta: dict) -> str:
    """Resolve the ``@index`` API name from ``spotMeta``, with no hardcoded index."""
    token_indices = {
        str(item.get("name", "")).upper(): item.get("index")
        for item in spot_meta.get("tokens", [])
    }
    base_index = token_indices.get(token.upper())
    quote_index = token_indices.get(quote.upper())
    if base_index is None or quote_index is None:
        raise ValueError(f"spot token not found: {token}/{quote}")
    for market in spot_meta.get("universe", []):
        if market.get("tokens") == [base_index, quote_index]:
            name = market.get("name")
            if name:
                return str(name)
    raise ValueError(f"spot pair not found: {token}/{quote}")


def normalize_closed_candles(rows: list[dict], *, now_ms: int) -> list[dict]:
    """Sort, deduplicate and drop the candle that is still open."""
    by_open_time: dict[int, dict] = {}
    for row in rows:
        open_time = int(row["t"])
        close_time = int(row["T"])
        if close_time >= now_ms:
            continue
        by_open_time[open_time] = {
            "timestamp": open_time // 1000,
            "open": float(row["o"]),
            "high": float(row["h"]),
            "low": float(row["l"]),
            "close": float(row["c"]),
        }
    return [by_open_time[key] for key in sorted(by_open_time)]


def validate_continuity(records: list[dict], interval_minutes: int) -> None:
    """The replay assumes evenly spaced bars; it refuses datasets with gaps."""
    expected_seconds = interval_minutes * 60
    for previous, current in zip(records, records[1:]):
        actual_seconds = current["timestamp"] - previous["timestamp"]
        if actual_seconds != expected_seconds:
            raise ValueError(
                "dataset discontinuu: "
                f"{previous['timestamp']} -> {current['timestamp']} "
                f"({actual_seconds}s, expected {expected_seconds}s)"
            )


def fetch_closed_candles(
    coin: str,
    interval_minutes: int,
    *,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    interval = INTERVALS.get(interval_minutes)
    if interval is None:
        raise ValueError(f"interval Hyperliquid nesuportat: {interval_minutes} minute")
    payload = _post({
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    })
    if not isinstance(payload, list):
        raise RuntimeError(f"invalid candleSnapshot response: {payload!r}")
    return normalize_closed_candles(payload, now_ms=end_ms)


def _parse_intervals(value: str) -> list[int]:
    try:
        intervals = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("intervals must be in minutes") from exc
    unsupported = [value for value in intervals if value not in INTERVALS]
    if not intervals or unsupported:
        raise argparse.ArgumentTypeError(
            f"intervale nesuportate: {unsupported or value}; suportate: {sorted(INTERVALS)}"
        )
    return intervals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", default="HYPE")
    parser.add_argument("--quote", default="USDC")
    parser.add_argument("--intervals", type=_parse_intervals, default=[60, 240, 1440])
    parser.add_argument(
        "--lookback-days", type=int,
        help="limit every interval to the same period; defaults to the API maximum",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("offline/results/hyperliquid_proxy/datasets"),
    )
    args = parser.parse_args()
    if args.lookback_days is not None and args.lookback_days <= 0:
        parser.error("--lookback-days must be positive")

    generated_at = dt.datetime.now(tz=dt.timezone.utc)
    end_ms = int(generated_at.timestamp() * 1000)
    start_ms = (
        end_ms - args.lookback_days * 24 * 60 * 60 * 1000
        if args.lookback_days is not None else 0
    )
    spot_meta = _post({"type": "spotMeta"})
    if not isinstance(spot_meta, dict):
        raise RuntimeError("invalid spotMeta response")
    coin = resolve_spot_pair(args.token, args.quote, spot_meta)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": 1,
        "generated_at_utc": generated_at.isoformat(),
        "source": {
            "venue": "Hyperliquid",
            "market": f"{args.token.upper()}/{args.quote.upper()} spot",
            "api_coin": coin,
            "endpoint": API_URL,
            "documentation": (
                "https://hyperliquid.gitbook.io/hyperliquid-docs/"
                "for-developers/api/info-endpoint"
            ),
        },
        "purpose": "cross-venue robustness proxy for Kraken strategy replay",
        "limitations": [
            "not Kraken market or execution data",
            "does not reproduce Kraken spread, queue position, latency, or fills",
            "Hyperliquid exposes at most the most recent 5000 candles per market",
        ],
        "requested_lookback_days": args.lookback_days,
        "datasets": {},
    }
    stem = f"{args.token.upper()}{args.quote.upper()}"
    for interval in args.intervals:
        records = fetch_closed_candles(
            coin, interval, start_ms=start_ms, end_ms=end_ms,
        )
        if not records:
            raise RuntimeError(f"dataset gol pentru {coin} {interval}m")
        validate_continuity(records, interval)
        raw = canonical_bytes(records)
        digest = dataset_sha256(records)
        path = output_dir / f"{stem}_{interval}m_hlspot_{digest[:12]}.csv"
        path.write_bytes(raw)
        manifest["datasets"][str(interval)] = {
            "file": str(path),
            "sha256": digest,
            "bars": len(records),
            "start_utc": dt.datetime.fromtimestamp(
                records[0]["timestamp"], tz=dt.timezone.utc,
            ).isoformat(),
            "end_utc": dt.datetime.fromtimestamp(
                records[-1]["timestamp"], tz=dt.timezone.utc,
            ).isoformat(),
        }
        print(f"{interval}m: {len(records)} bare -> {path}")

    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    manifest_path = output_dir.parent / f"manifest_{stamp}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
