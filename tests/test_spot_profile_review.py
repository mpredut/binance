"""Profile comparison parses versioned configuration without leaking environment."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from offline.runners.spot_profile_review import revision_params


def test_versioned_profile_is_isolated_from_the_calling_environment():
    config = (Path(__file__).resolve().parents[1] / "hyperliquid/config.env").read_text()
    with patch.dict(os.environ, STRAT_ENTRY="999999"), patch(
            "offline.runners.spot_profile_review.subprocess.run",
            return_value=SimpleNamespace(stdout=config)):
        params = revision_params("test:hyperliquid/config.env")
        assert params.entry_amount == 350
        assert os.environ["STRAT_ENTRY"] == "999999"
