"""Minimal atomic state store for Assetguardian tranches."""
import os
from state_io import atomic_write_json, load_json_state

from lock import FileLock


class AssetGuardianState:
    def __init__(self, path=None, *, fail_closed=True):
        root = os.path.dirname(os.path.abspath(__file__))
        self.path = path or os.path.join(root, "cachedb", "assetguardian_state.json")
        self.lock_path = self.path + ".lock"
        self.fail_closed = fail_closed

    def load(self):
        with FileLock(self.lock_path):
            return load_json_state(
                self.path, default_factory=dict, fail_closed=self.fail_closed,
                label="AssetGuardian",
            )

    def save(self, value):
        with FileLock(self.lock_path):
            directory = os.path.dirname(self.path)
            os.makedirs(directory, exist_ok=True)
            atomic_write_json(
                self.path, value, sort_keys=True, separators=(",", ":"),
            )
