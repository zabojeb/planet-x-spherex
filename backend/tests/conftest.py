"""Shared test setup."""

import pytest

from spherex_core import netpolicy
from spherex_core.analysis import common


@pytest.fixture(autouse=True)
def _fresh_network_state():
    """A remote service that failed in one test must not be skipped in the next, and sequence ids reused by
    different tests' temporary caches must not share memoised frame lists."""
    netpolicy.reset()
    common._FRAMES_MEMO.clear()
    yield
    netpolicy.reset()
    common._FRAMES_MEMO.clear()
