"""Minimal atomic state store for Assetguardian tranches."""
import json
import os
import tempfile

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
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(value, handle, sort_keys=True, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
