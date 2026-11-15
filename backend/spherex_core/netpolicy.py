"""How long to wait for remote services, and when to stop trying them.

Background builds can afford IRSA's slow days (minutes per TAP query). Interactive work cannot: a user who
clicks "Analyze" should get an answer in seconds, with "unavailable" where a service is down. Two tools:

- `fast()` marks the current context (and threads started with `run_in_context`) as interactive: remote calls
  then use short timeouts (FAST_TIMEOUT_S) and at most FAST_ATTEMPTS attempts with a short pause.
- a per-service circuit breaker: after a failure the service is skipped for BREAKER_OPEN_S, so the next
  interactive call fails at once (`ServiceUnavailable`) instead of waiting for another timeout. Successes close
  it. Background work records failures but never skips a service.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from collections.abc import Callable, Iterator
from typing import TypeVar

FAST_TIMEOUT_S = 10.0  # two attempts fit the 20 s catalogue budget of a point analysis
FAST_ATTEMPTS = 2
FAST_PAUSE_S = 0.5
BREAKER_OPEN_S = 300.0

_fast: contextvars.ContextVar[bool] = contextvars.ContextVar("spherex_fast_network", default=False)
_lock = threading.Lock()
_open_until: dict[str, float] = {}
_last_error: dict[str, str] = {}
T = TypeVar("T")


class ServiceUnavailable(RuntimeError):
    """A remote service failed recently; interactive callers skip it until the breaker closes."""


@contextlib.contextmanager
def fast() -> Iterator[None]:
    """Interactive mode for the current context: short timeouts, few attempts, skip services known to be down."""
    token = _fast.set(True)
    try:
        yield
    finally:
        _fast.reset(token)


def is_fast() -> bool:
    return _fast.get()


def run_in_context(fn: Callable[..., T]) -> Callable[..., T]:
    """Wrap `fn` so it runs with a copy of the caller's context (thread pools do not copy contextvars)."""
    ctx = contextvars.copy_context()
    return lambda *a, **kw: ctx.run(fn, *a, **kw)


def timeouts(timeout: float, attempts: int) -> tuple[float, int]:
    """(timeout, attempts) for a remote call: the caller's values, clamped in interactive mode."""
    if is_fast():
        return min(timeout, FAST_TIMEOUT_S), max(1, min(attempts, FAST_ATTEMPTS))
    return timeout, attempts


def pause(seconds: float) -> float:
    """Back-off between attempts: short in interactive mode."""
    return min(seconds, FAST_PAUSE_S) if is_fast() else seconds


def check(service: str) -> None:
    """Raise ServiceUnavailable in interactive mode when `service` failed within BREAKER_OPEN_S."""
    if not is_fast():
        return
    with _lock:
        until = _open_until.get(service, 0.0)
        err = _last_error.get(service, "")
    if until > time.monotonic():
        raise ServiceUnavailable(f"{service} is unavailable (failed {int(BREAKER_OPEN_S - (until - time.monotonic()))}"
                                 f" s ago: {err}); skipped, retry in {int(until - time.monotonic())} s")


def failed(service: str, error: BaseException | str) -> None:
    with _lock:
        _open_until[service] = time.monotonic() + BREAKER_OPEN_S
        _last_error[service] = (str(error).splitlines() or [error.__class__.__name__])[0][:120] \
            if not isinstance(error, str) else error[:120]


def succeeded(service: str) -> None:
    with _lock:
        _open_until.pop(service, None)
        _last_error.pop(service, None)


def status() -> dict[str, dict]:
    """Services whose breaker is open, with seconds left and the last error (for /api/health and logs)."""
    now = time.monotonic()
    with _lock:
        return {s: {"retry_in_s": round(u - now), "error": _last_error.get(s, "")}
                for s, u in _open_until.items() if u > now}


def reset() -> None:
    with _lock:
        _open_until.clear()
        _last_error.clear()
