import os
import sys

import pytest

from shadow_runtime import prepare_shadow_runtime, require_shadow_interval


def test_prepare_shadow_runtime_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("BINANCE_AUTO_START_WEBSOCKETS", raising=False)
    path = str(tmp_path)
    prepare_shadow_runtime(path)
    prepare_shadow_runtime(path)
    assert os.environ["BINANCE_AUTO_START_WEBSOCKETS"] == "0"
    assert sys.path.count(path) == 1


def test_shadow_interval_validation():
    require_shadow_interval(240, 240, "HL shadow")
    with pytest.raises(ValueError, match="240m"):
        require_shadow_interval(60, 240, "HL shadow")
