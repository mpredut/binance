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
import shutil
import tempfile
from collections.abc import Iterator
from typing import Any


class StateReadError(RuntimeError):
    """Persisted runtime state cannot be trusted."""


def _fsync_parent_directory(path: str) -> None:
    """Best-effort directory fsync after publishing a new directory entry."""
    directory = os.path.dirname(os.path.abspath(os.fspath(path)))
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # Directory handles are not portable (notably on native Windows).
        pass


def durable_replace_file(source: str, destination: str) -> None:
    """Atomically replace ``destination`` and durably publish the new entry."""
    source = os.path.abspath(os.fspath(source))
    destination = os.path.abspath(os.fspath(destination))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    os.replace(source, destination)
    with open(destination, "rb") as handle:
        os.fsync(handle.fileno())
    _fsync_parent_directory(destination)


def atomic_snapshot_file(source: str, destination: str) -> None:
    """Atomically preserve one immutable file generation at ``destination``.

    A hard link avoids copying a potentially large generation when supported;
    otherwise the bytes are copied and fsynced. Callers must publish subsequent
    source generations by replacement rather than mutating the source in place.
    """
    source = os.path.abspath(os.fspath(source))
    destination = os.path.abspath(os.fspath(destination))
    directory = os.path.dirname(destination)
    os.makedirs(directory, exist_ok=True)
    staging_directory = tempfile.mkdtemp(
        dir=directory, prefix=f".{os.path.basename(destination)}."
    )
    staged = os.path.join(staging_directory, "generation")
    try:
        try:
            os.link(source, staged)
        except OSError:
            shutil.copyfile(source, staged)
            with open(staged, "rb") as handle:
                os.fsync(handle.fileno())
        durable_replace_file(staged, destination)
    finally:
        try:
            os.unlink(staged)
        except OSError:
            pass
        try:
            os.rmdir(staging_directory)
        except OSError:
            pass


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
        _fsync_parent_directory(destination)
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
