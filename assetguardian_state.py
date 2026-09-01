"""Minimal atomic state store for Assetguardian tranches."""
import json
import os
from state_io import atomic_write_json

from lock import FileLock


class AssetGuardianState:
    def __init__(self, path=None):
        root = os.path.dirname(os.path.abspath(__file__))
        self.path = path or os.path.join(root, "cachedb", "assetguardian_state.json")
        self.lock_path = self.path + ".lock"

    def load(self):
        with FileLock(self.lock_path):
            try:
                with open(self.path, encoding="utf-8") as handle:
                    value = json.load(handle)
                return value if isinstance(value, dict) else {}
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return {}

    def save(self, value):
        with FileLock(self.lock_path):
            directory = os.path.dirname(self.path)
            os.makedirs(directory, exist_ok=True)
            atomic_write_json(
                self.path, value, sort_keys=True, separators=(",", ":"),
            )
