"""Keep every offline test out of the user's real account runtime."""

import pytest


@pytest.fixture(autouse=True)
def isolated_account_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("SAU_RUNTIME_DIR", str(tmp_path / "account-runtime"))
