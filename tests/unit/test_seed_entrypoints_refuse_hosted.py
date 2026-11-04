"""The seed scripts refuse a hosted database unless it is confirmed, before they connect."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "dev"
HOSTED = "postgresql+asyncpg://user:secret@db.example.invalid:5432/kvarteret"


@pytest.mark.parametrize("script", ["seed_synthetic.py", "seed_shifts.py", "bootstrap.py"])
def test_hosted_database_without_confirmation_is_refused(script: str) -> None:
    env = {k: v for k, v in os.environ.items() if k != "DEMO_SEED_CONFIRM"}
    env["DEV_DATABASE_URL"] = HOSTED
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / script)], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode != 0
    assert "Refused" in done.stderr
    assert "DEMO_SEED_CONFIRM=user@db.example.invalid:5432/kvarteret" in done.stderr
    assert "secret" not in done.stderr
