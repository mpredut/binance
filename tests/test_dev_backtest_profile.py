import os
from pathlib import Path
import subprocess

from botcore import parse_dotenv


ROOT = Path(__file__).resolve().parents[1]
RUNNERS = ROOT / "offline" / "runners"


def test_versioned_dev_profile_has_every_required_field():
    profile = parse_dotenv(str(RUNNERS / "dev_backtest.env"))
    required = {
        "DEV_HOST", "DEV_PORT", "DEV_USER", "DEV_PATH",
        "DEV_CODE_BRANCH", "BACKTEST_PROPOSALS_BRANCH",
    }
    assert required <= profile.keys()
    assert all(profile[key].strip() for key in required)


def test_shell_profile_loader_exports_the_versioned_values():
    env = dict(os.environ, RUNNER_DIR=str(RUNNERS))
    result = subprocess.run(
        ["bash", "-c", "source offline/runners/load_dev_backtest_env.sh; "
         "printf '%s|%s|%s' \"$DEV_HOST\" \"$DEV_CODE_BRANCH\" "
         "\"$BACKTEST_PROPOSALS_BRANCH\""],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )
    profile = parse_dotenv(str(RUNNERS / "dev_backtest.env"))
    assert result.stdout == "|".join([
        profile["DEV_HOST"], profile["DEV_CODE_BRANCH"],
        profile["BACKTEST_PROPOSALS_BRANCH"],
    ])
