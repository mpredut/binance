"""Persistenta JSON comuna pentru starea motoarelor financiare.

Scrierea este atomica in acelasi director, iar runtime-urile live pot cere
fail-closed: o stare corupta sau imposibil de salvat nu este confundata cu o
stare goala care ar putea permite o intrare duplicata.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable


class StatePersistenceError(RuntimeError):
    """Starea financiara nu poate fi citita sau persistata in siguranta."""


class JsonStateStore:
    def __init__(self, path: str, default_factory: Callable[[], dict], *,
                 label: str, logger: Callable[[str], None], fail_closed: bool):
        self.path = path
        self.default_factory = default_factory
        self.label = label
        self.logger = logger
        self.fail_closed = fail_closed

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return self.default_factory()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("radacina JSON nu este obiect")
            merged = self.default_factory()
            if not isinstance(merged, dict):
                raise TypeError("default_factory nu a returnat dict")
            merged.update(loaded)
            self.logger(
                f"  [STRAT] stare incarcata (ciclu {merged.get('cycle')}, "
                f"qty {merged.get('qty')})"
            )
            return merged
        except (OSError, TypeError, ValueError) as exc:
            message = f"stare {self.label} invalida in {self.path}: {exc}"
            if self.fail_closed:
                raise StatePersistenceError(message) from exc
            self.logger(f"  ! [STRAT] {message}; reset permis doar in PAPER")
            return self.default_factory()

    def save(self, state: dict) -> bool:
        temporary = f"{self.path}.tmp.{os.getpid()}"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            return True
        except (OSError, TypeError, ValueError) as exc:
            self.logger(f"  ! [STRAT] nu pot salva starea: {exc}")
            try:
                os.remove(temporary)
            except OSError:
                pass
            if self.fail_closed:
                raise StatePersistenceError(
                    f"persistenta starii {self.label} a esuat: {exc}"
                ) from exc
            return False
