from __future__ import annotations

import sys

import pytest

from outocut_engine.security import protect_secret, unprotect_secret


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_round_trip() -> None:
    encrypted = protect_secret("sk-sensitive-test-value")
    assert "sk-sensitive" not in encrypted
    assert unprotect_secret(encrypted) == "sk-sensitive-test-value"
