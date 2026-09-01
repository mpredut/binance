"""Shared durable file primitives for runtime and financial state.

Writers use a unique temporary file in the destination directory, flush file data,
atomically replace the destination, and clean up on failure. Locking and failure
policy remain the caller's responsibility because caches, live financial state,
and observational state intentionally have different concurrency semantics.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterator
from typing import Any


class StateReadError(RuntimeError):
    """Persisted runtime state cannot be trusted."""


def load_json_state(path: str, *, default_factory, fail_closed: bool,
                    label: str, root_type: type = dict):
    """Load JSON state with an explicit live-versus-observational failure policy.

    A missing file represents first start and returns the factory default. Invalid,
    unreadable, or structurally wrong state raises in fail-closed mode; paper,
    shadow, and monitoring callers may explicitly opt into a clean default.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, root_type):
            raise ValueError(
                f"expected {root_type.__name__}, got {type(value).__name__}"
            )
        return value
    except FileNotFoundError:
        return default_factory()
    except (OSError, TypeError, ValueError) as exc:
        if fail_closed:
            raise StateReadError(f"invalid {label} state in {path}: {exc}") from exc
        return default_factory()


@contextlib.contextmanager
def atomic_text_writer(path: str, *, encoding: str = "utf-8") -> Iterator[Any]:
    """Yield a text handle and atomically publish it only after a successful write."""
    destination = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(destination)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=directory, prefix=f".{os.path.basename(destination)}.", suffix=".tmp",
    )
    handle = os.fdopen(fd, "w", encoding=encoding)
    try:
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary, destination)
    except BaseException:
        handle.close()
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, value: Any, *, indent: int | None = None,
                      sort_keys: bool = False, separators=None,
                      ensure_ascii: bool = True) -> None:
    """Serialize JSON through ``atomic_text_writer``."""
    with atomic_text_writer(path) as handle:
        json.dump(
            value, handle, indent=indent, sort_keys=sort_keys,
            separators=separators, ensure_ascii=ensure_ascii,
        )
