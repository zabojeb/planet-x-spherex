"""Remote services as interchangeable endpoints, ordered by how well they have been answering.

Every kind of data we fetch from outside has more than one source (SPHEREx files on AWS S3 and at IRSA, Gaia DR3
at ESA, MAST, IRSA and VizieR, ...). Each call records its latency and outcome per endpoint; `ordered(kind)` puts
the endpoints that answered recently and quickly first, so the fastest healthy source is the default and a dead
one sinks to the end without a config change. `call(kind, fn)` tries them in that order; in interactive mode
(netpolicy.fast) it can *hedge*: when the first endpoint has not answered by about its 90th-percentile latency,
the second one is started too and the first answer wins.

Statistics live in-process (a rolling window per endpoint). With REDIS_URL set, every sample is also pushed to
Redis so `status()` on any API replica shows what all replicas observed; the ordering itself stays local, which
keeps the hot path free of network round trips.
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import contextvars
import logging
import os
import statistics
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from . import netpolicy

log = logging.getLogger(__name__)
T = TypeVar("T")
WINDOW = 50  # samples kept per endpoint
FAIL_PENALTY_S = 60.0  # how much a recent failure "costs" in the ordering score
FAIL_MEMORY_S = 600.0  # failures older than this no longer push an endpoint down
HEDGE_MIN_S = 0.6
HEDGE_MAX_S = 8.0


@dataclass(frozen=True)
class Endpoint:
    """One source for one kind of data. `name` is unique; `service` is the breaker key (usually the host)."""

    name: str
    kind: str
    service: str
    label: str = ""
    note: str = ""  # e.g. "geocentric positions" for a source with known caveats
    concurrency: int = 0  # suggested parallel requests per process (0 = caller's default)
    priority: int = 0  # static tie-breaker: lower is preferred when there is no data yet


@dataclass
class _Stats:
    latencies: collections.deque = field(default_factory=lambda: collections.deque(maxlen=WINDOW))
    outcomes: collections.deque = field(default_factory=lambda: collections.deque(maxlen=WINDOW))  # (t, ok)
    last_error: str = ""
    last_ok: float = 0.0
    last_fail: float = 0.0


_lock = threading.Lock()
_registry: dict[str, list[Endpoint]] = {}
_stats: dict[str, _Stats] = collections.defaultdict(_Stats)
_redis = None
_redis_tried = False


def register(*endpoints: Endpoint) -> None:
    """Add endpoints (idempotent by name)."""
    with _lock:
        for ep in endpoints:
            lst = _registry.setdefault(ep.kind, [])
            if all(e.name != ep.name for e in lst):
                lst.append(ep)


def endpoints(kind: str) -> list[Endpoint]:
    with _lock:
        return list(_registry.get(kind, []))


def record(ep: Endpoint | str, seconds: float, ok: bool, error: BaseException | str | None = None,
           breaker: bool = True) -> None:
    """One observation. Also feeds the netpolicy circuit breaker of the endpoint's service unless breaker=False
    (for services where a refusal is routine, like IRSA's busy cutout service)."""
    name = ep if isinstance(ep, str) else ep.name
    service = None if isinstance(ep, str) else ep.service
    now = time.time()
    with _lock:
        st = _stats[name]
        st.outcomes.append((now, ok))
        if ok:
            st.latencies.append(seconds)
            st.last_ok = now
        else:
            st.last_fail = now
            st.last_error = (str(error).splitlines() or [""])[0][:160] if error is not None else "failed"
    if service and breaker:
        if ok:
            netpolicy.succeeded(service)
        else:
            netpolicy.failed(service, error or "failed")
    _start_flusher()


def mostly_failing(ep: Endpoint, min_samples: int = 10, threshold: float = 0.3, window_s: float = 300.0) -> bool:
    """True when `ep` succeeded in fewer than `threshold` of at least `min_samples` recent requests."""
    now = time.time()
    with _lock:
        st = _stats.get(ep.name)
        recent = [ok for t, ok in (st.outcomes if st else []) if now - t < window_s]
    return len(recent) >= min_samples and recent.count(True) / len(recent) < threshold


def _percentile(xs, q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def score(ep: Endpoint) -> float:
    """Lower is better: median latency plus a penalty for recent failures; unknown endpoints sit in the middle."""
    now = time.time()
    with _lock:
        st = _stats.get(ep.name)
        lat = list(st.latencies) if st else []
        recent = [ok for t, ok in (st.outcomes if st else []) if now - t < FAIL_MEMORY_S]
    p50 = statistics.median(lat) if lat else 1.0 + ep.priority  # never measured: neutral, by static priority
    fails = recent.count(False)
    rate = fails / len(recent) if recent else 0.0
    open_breaker = ep.service in netpolicy.status()
    return p50 + FAIL_PENALTY_S * rate + (10 * FAIL_PENALTY_S if open_breaker else 0.0) + 0.001 * ep.priority


def ordered(kind: str) -> list[Endpoint]:
    """Endpoints of `kind`, best first (see score)."""
    return sorted(endpoints(kind), key=score)


def hedge_delay(ep: Endpoint) -> float:
    """How long to give `ep` before starting the next endpoint in interactive mode: about its p90 latency."""
    with _lock:
        lat = list(_stats[ep.name].latencies)
    p90 = _percentile(lat, 0.9)
    return min(max(1.5 * p90, HEDGE_MIN_S) if p90 is not None else 2.0, HEDGE_MAX_S)


# ---------------------------------------------------------------- calling


class AllFailed(RuntimeError):
    """Every endpoint of a kind failed; `errors` maps endpoint name -> message."""

    def __init__(self, kind: str, errors: dict[str, str]):
        self.kind, self.errors = kind, errors
        super().__init__(f"all {kind} sources failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))


def _timed(ep: Endpoint, fn: Callable[[Endpoint], T]) -> T:
    t0 = time.perf_counter()
    try:
        out = fn(ep)
    except netpolicy.ServiceUnavailable:
        raise  # skipped, not a fresh failure
    except Exception as e:
        if not getattr(e, "health_neutral", False):  # e.g. "this mirror does not have the file"
            record(ep, time.perf_counter() - t0, False, e)
        raise
    record(ep, time.perf_counter() - t0, True)
    return out


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="upstream")
_has_fallback: contextvars.ContextVar[bool] = contextvars.ContextVar("spherex_has_fallback", default=False)
_last_resort: contextvars.ContextVar[bool] = contextvars.ContextVar("spherex_last_resort", default=False)
FALLBACK_TIMEOUT_S = 45.0
LAST_RESORT_TIMEOUT_S = 30.0


def has_fallback() -> bool:
    """True inside a `call` attempt that is not the last source: clients should give up quickly (one attempt,
    FALLBACK_TIMEOUT_S) and let the next source answer instead of retrying a dying service for minutes."""
    return _has_fallback.get()


def last_resort() -> bool:
    """True inside the attempt on the last source after the others failed, were skipped or are running slow:
    an interactive client should make one longer attempt (LAST_RESORT_TIMEOUT_S) rather than two short ones
    that a slow but working service cannot finish (NOIRLab Data Lab needs ~15 s for a CatWISE cone)."""
    return _last_resort.get()


def _attempt(ep: Endpoint, fn, last: bool, resort: bool = False):
    token, token2 = _has_fallback.set(not last), _last_resort.set(resort)
    try:
        return _timed(ep, fn)
    finally:
        _has_fallback.reset(token)
        _last_resort.reset(token2)


def call(kind: str, fn: Callable[[Endpoint], T], *, hedge: bool | None = None,
         only: Callable[[Endpoint], bool] | None = None) -> tuple[T, Endpoint]:
    """Run `fn(endpoint)` on the endpoints of `kind`, best first, until one succeeds.

    hedge: None = hedge in interactive mode (netpolicy.fast). With hedging the next endpoint starts when the
    current one runs past its hedge delay; the first success wins (the loser keeps running in the background
    and only updates the statistics). Raises AllFailed when none succeeds.
    """
    eps = [e for e in ordered(kind) if only is None or only(e)]
    if not eps:
        raise AllFailed(kind, {"-": "no endpoint registered"})
    hedge = netpolicy.is_fast() if hedge is None else hedge
    errors: dict[str, str] = {}
    live = []  # sources with an open breaker are dropped first, so the flags below see the real last source
    for ep in eps:
        try:
            netpolicy.check(ep.service)
            live.append(ep)
        except netpolicy.ServiceUnavailable as e:
            errors[ep.name] = str(e)[:160]
    if not live:
        raise AllFailed(kind, errors)
    others = len(eps) > 1  # the last live source is a last resort only if other sources exist

    if not hedge or len(live) == 1:
        for i, ep in enumerate(live):
            last = i == len(live) - 1
            try:
                return _attempt(ep, fn, last, last and others), ep
            except Exception as e:  # noqa: BLE001 - try the next source
                errors[ep.name] = (str(e).splitlines() or [e.__class__.__name__])[0][:160]
        raise AllFailed(kind, errors)

    pending: dict[concurrent.futures.Future, Endpoint] = {}
    queue = list(live)
    while queue or pending:
        if queue and (not pending):
            ep = queue.pop(0)
            pending[_pool.submit(netpolicy.run_in_context(_attempt), ep, fn, not queue, not queue and others)] = ep
        wait_for = hedge_delay(next(iter(pending.values()))) if queue else None
        done, _ = concurrent.futures.wait(pending, timeout=wait_for, return_when=concurrent.futures.FIRST_COMPLETED)
        if not done and queue:  # the current endpoint is slow: start the next one alongside
            ep = queue.pop(0)  # (the last one started this way is also a last resort)
            log.info("hedging %s: %s slow, also asking %s", kind, ", ".join(e.name for e in pending.values()), ep.name)
            pending[_pool.submit(netpolicy.run_in_context(_attempt), ep, fn, not queue, not queue)] = ep
            continue
        for fut in done:
            ep = pending.pop(fut)
            try:
                return fut.result(), ep
            except Exception as e:  # noqa: BLE001
                errors[ep.name] = (str(e).splitlines() or [e.__class__.__name__])[0][:160]
    raise AllFailed(kind, errors)


async def call_async(kind: str, fn: Callable[[Endpoint], Awaitable[T]], *, hedge: bool | None = None,
                     only: Callable[[Endpoint], bool] | None = None) -> tuple[T, Endpoint]:
    """Async twin of `call` for coroutines (byte-range reads)."""
    eps = [e for e in ordered(kind) if only is None or only(e)]
    if not eps:
        raise AllFailed(kind, {"-": "no endpoint registered"})
    hedge = netpolicy.is_fast() if hedge is None else hedge
    errors: dict[str, str] = {}

    async def timed(ep: Endpoint) -> T:
        t0 = time.perf_counter()
        try:
            out = await fn(ep)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not getattr(e, "health_neutral", False):
                record(ep, time.perf_counter() - t0, False, e)
            raise
        record(ep, time.perf_counter() - t0, True)
        return out

    queue = list(eps)
    pending: dict[asyncio.Task, Endpoint] = {}
    try:
        while queue or pending:
            if queue and not pending:
                ep = queue.pop(0)
                pending[asyncio.ensure_future(timed(ep))] = ep
            wait_for = hedge_delay(next(iter(pending.values()))) if (hedge and queue) else None
            done, _ = await asyncio.wait(pending, timeout=wait_for, return_when=asyncio.FIRST_COMPLETED)
            if not done and queue:
                ep = queue.pop(0)
                pending[asyncio.ensure_future(timed(ep))] = ep
                continue
            for t in done:
                ep = pending.pop(t)
                try:
                    return t.result(), ep
                except Exception as e:  # noqa: BLE001
                    errors[ep.name] = (str(e).splitlines() or [e.__class__.__name__])[0][:160]
        raise AllFailed(kind, errors)
    finally:
        for t in pending:  # a hedge loser: stop it, its answer is no longer needed
            t.cancel()


# ---------------------------------------------------------------- status


def status() -> dict:
    """Per kind: endpoints in the current order with health and latency (for /api/status/upstreams)."""
    now = time.time()
    open_breakers = netpolicy.status()
    out: dict[str, list[dict]] = {}
    for kind in sorted(_registry):
        rows = []
        for rank, ep in enumerate(ordered(kind)):
            with _lock:
                st = _stats.get(ep.name) or _Stats()
                lat = list(st.latencies)
                recent = [ok for t, ok in st.outcomes if now - t < FAIL_MEMORY_S]
            rows.append({
                "rank": rank, "name": ep.name, "label": ep.label or ep.name, "service": ep.service, "note": ep.note,
                "samples": len(st.outcomes), "success_rate": round(recent.count(True) / len(recent), 3) if recent else None,
                "p50_s": round(statistics.median(lat), 3) if lat else None,
                "p90_s": round(_percentile(lat, 0.9), 3) if lat else None,
                "last_ok_s_ago": round(now - st.last_ok) if st.last_ok else None,
                "last_fail_s_ago": round(now - st.last_fail) if st.last_fail else None,
                "last_error": st.last_error or None,
                "breaker": open_breakers.get(ep.service),
                "state": "down" if ep.service in open_breakers else ("ok" if recent and recent[-1] else
                                                                    ("unknown" if not recent else "degraded")),
            })
        out[kind] = rows
    shared = _redis_summary()
    return {"kinds": out, "cluster": shared} if shared else {"kinds": out}


def reset() -> None:
    with _lock:
        _stats.clear()


_flusher_started = False
REPLICA = f"{os.uname().nodename}:{os.getpid()}"


def _start_flusher() -> None:
    """With Redis configured, publish this process's per-endpoint summary every 15 s (never on the hot path)."""
    global _flusher_started
    if _flusher_started:
        return
    _flusher_started = True
    if not (os.environ.get("REDIS_URL") or os.environ.get("SPHEREX_REDIS_URL")):
        return

    def loop():
        import json

        while True:
            time.sleep(15)
            r = _get_redis()
            if r is None:
                continue
            try:
                summary = {}
                with _lock:
                    for name, st in _stats.items():
                        lat = list(st.latencies)
                        ok = [o for _, o in st.outcomes]
                        summary[name] = {"samples": len(ok), "success_rate": round(sum(ok) / len(ok), 3) if ok else None,
                                         "p50_s": round(statistics.median(lat), 3) if lat else None, "t": time.time()}
                r.hset("spherex:upstreams", REPLICA, json.dumps(summary))
                r.expire("spherex:upstreams", 3600)
            except Exception:  # noqa: BLE001 - statistics must never break anything
                pass

    threading.Thread(target=loop, name="upstream-stats", daemon=True).start()


def _redis_summary() -> dict | None:
    """Per replica: what every API/worker process observed in its last 15-s report (needs Redis)."""
    r = _get_redis()
    if r is None:
        return None
    try:
        import json

        now = time.time()
        out = {}
        for replica, raw in r.hgetall("spherex:upstreams").items():
            data = json.loads(raw)
            if data and now - max(v.get("t", 0) for v in data.values()) < 600:
                out[replica] = data
        return out
    except Exception:  # noqa: BLE001
        return None


def _get_redis():
    global _redis, _redis_tried
    if _redis_tried:
        return _redis
    _redis_tried = True
    url = os.environ.get("REDIS_URL") or os.environ.get("SPHEREX_REDIS_URL")
    if not url:
        return None
    try:
        import redis  # sync client; only used for statistics

        _redis = redis.Redis.from_url(url, decode_responses=True, socket_timeout=0.5, socket_connect_timeout=0.5)
        _redis.ping()
    except Exception:  # noqa: BLE001
        _redis = None
    return _redis
