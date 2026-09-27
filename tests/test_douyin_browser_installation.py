"""Missing browser packages must not prevent retrying account maintenance."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("operation,existing_session", (("login", False), ("login", True), ("check", True)))
def test_douyin_maintenance_can_retry_when_chromium_is_missing(tmp_path, operation, existing_session):
    project = Path(__file__).resolve().parents[1]
    shutil.copy2(project / "sau_cli.py", tmp_path / "sau_cli.py")
    shutil.copy2(project / "conf.example.py", tmp_path / "conf.py")
    account_file = tmp_path / "cookies" / "douyin_creator.json"
    session = '{"cookies": [], "origins": []}'
    if existing_session:
        account_file.parent.mkdir()
        account_file.write_text(session, encoding="utf-8")
    environment = dict(
        os.environ,
        SAU_RUNTIME_DIR=str(tmp_path / "runtime"),
        PLAYWRIGHT_BROWSERS_PATH=str(tmp_path / "absent-browsers"),
        PYTHONPATH=str(project),
    )
    outputs = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(tmp_path / "sau_cli.py"),
             "douyin", operation, "--account", "creator",
             *(["--headed"] if operation == "login" else [])],
            cwd=tmp_path, env=environment, capture_output=True, text=True,
            encoding="utf-8", stdin=subprocess.DEVNULL, timeout=20,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 1, output
        assert "previous browser shutdown" not in output.lower(), output
        assert "previous account executor" not in output.lower(), output
        outputs.append(output)
    for output in outputs:
        assert "python -m patchright install --force chromium" in output
    if existing_session:
        assert account_file.read_text(encoding="utf-8") == session
    else:
        assert not account_file.exists()
