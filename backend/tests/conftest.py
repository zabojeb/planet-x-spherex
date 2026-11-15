"""Shared test setup."""

import pytest

from spherex_core import netpolicy
from spherex_core.analysis import common


@pytest.fixture(autouse=True)
def _no_local_mirror(monkeypatch):
    """Tests never answer image searches from a file-catalogue mirror that happens to exist on the machine;
    tests about the mirror patch mirror.get themselves."""
    from spherex_core.archive import mirror

    monkeypatch.setattr(mirror, "get", lambda: None)


@pytest.fixture(autouse=True)
def _fresh_network_state():
    """A remote service that failed in one test must not be skipped in the next, and sequence ids reused by
    different tests' temporary caches must not share memoised frame lists."""
    netpolicy.reset()
    common._FRAMES_MEMO.clear()
    yield
    netpolicy.reset()
    common._FRAMES_MEMO.clear()
