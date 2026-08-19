"""Dataset OHLC canonic, partajat de toate backtesterele offline.

Modulul nu cunoaște venue-uri sau strategii. El fixează doar contractul de date,
hash-ul reproductibil și validările care trebuie să fie identice pentru Kraken,
Trading212 și orice adaptor viitor.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close")


def normalize_record(record: Mapping) -> dict:
    """Normalizează un rând în contractul OHLC comun."""
    return {
        "timestamp": int(record["timestamp"]),
        "open": float(record["open"]),
        "high": float(record["high"]),
        "low": float(record["low"]),
        "close": float(record["close"]),
    }


def canonical_bytes(records: Iterable[Mapping]) -> bytes:
    """Serializare stabilă folosită atât la CSV, cât și la SHA-256."""
    lines = [",".join(REQUIRED_FIELDS)]
    for source in records:
        row = normalize_record(source)
        lines.append(
            f"{row['timestamp']},{row['open']:.12g},{row['high']:.12g},"
            f"{row['low']:.12g},{row['close']:.12g}"
        )
    return ("\n".join(lines) + "\n").encode("ascii")


def dataset_sha256(records: Iterable[Mapping]) -> str:
    return hashlib.sha256(canonical_bytes(records)).hexdigest()


def save_dataset(records: Iterable[Mapping], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(canonical_bytes(records))


def load_dataset(source: Path) -> list[dict]:
    with source.open("r", encoding="ascii", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != REQUIRED_FIELDS:
            raise ValueError(
                f"header OHLC invalid în {source}: {reader.fieldnames}; "
                f"așteptat {list(REQUIRED_FIELDS)}"
            )
        return [normalize_record(row) for row in reader]


def validate_dataset(records: Iterable[Mapping], *, interval_minutes: int | None = None) -> list[dict]:
    """Validează ordinea, cadența și invariantele OHLC; întoarce forma normalizată."""
    rows = [normalize_record(record) for record in records]
    if not rows:
        raise ValueError("dataset-ul OHLC nu poate fi gol")
    if interval_minutes is not None and interval_minutes <= 0:
        raise ValueError("interval_minutes trebuie să fie pozitiv")

    expected_delta = interval_minutes * 60 if interval_minutes is not None else None
    previous_timestamp = None
    for index, row in enumerate(rows, start=2):
        timestamp = row["timestamp"]
        if previous_timestamp is not None:
            delta = timestamp - previous_timestamp
            if delta <= 0:
                raise ValueError(f"timestamp neordonat/duplicat la linia {index}: {timestamp}")
            if expected_delta is not None and delta != expected_delta:
                raise ValueError(
                    f"cadență invalidă la linia {index}: {delta}s, așteptat {expected_delta}s"
                )
        previous_timestamp = timestamp

        open_, high, low, close = row["open"], row["high"], row["low"], row["close"]
        if min(open_, high, low, close) <= 0:
            raise ValueError(f"preț nepozitiv la linia {index}")
        if high < max(open_, close, low) or low > min(open_, close, high):
            raise ValueError(f"invariantă OHLC invalidă la linia {index}")
    return rows


def dataset_metadata(records: Iterable[Mapping], *, interval_minutes: int | None = None) -> dict:
    rows = validate_dataset(records, interval_minutes=interval_minutes)
    return {
        "sha256": dataset_sha256(rows),
        "bars": len(rows),
        "start_utc": dt.datetime.fromtimestamp(
            rows[0]["timestamp"], tz=dt.timezone.utc,
        ).isoformat(),
        "end_utc": dt.datetime.fromtimestamp(
            rows[-1]["timestamp"], tz=dt.timezone.utc,
        ).isoformat(),
    }


def align_previous_values(
    target_timestamps: Sequence[int],
    source_records: Iterable[Mapping],
    *,
    value_field: str = "close",
    transform: Callable[[float], float] | None = None,
) -> list[float]:
    """Aliniază as-of fără lookahead: ultima valoare cu timestamp <= țintă."""
    source = sorted(
        (
            int(record["timestamp"]),
            float(record[value_field]),
        )
        for record in source_records
    )
    if not source:
        raise ValueError("seria sursă pentru aliniere nu poate fi goală")
    convert = transform or (lambda value: value)
    result = []
    source_index = 0
    current: float | None = None
    for timestamp in target_timestamps:
        target = int(timestamp)
        while source_index < len(source) and source[source_index][0] <= target:
            current = convert(source[source_index][1])
            source_index += 1
        if current is None:
            raise ValueError(
                f"nu există valoare FX cunoscută la sau înainte de timestamp {target}; "
                "extinde datasetul FX în trecut"
            )
        if current <= 0:
            raise ValueError(f"valoare aliniată nepozitivă la timestamp {target}")
        result.append(float(current))
    return result
