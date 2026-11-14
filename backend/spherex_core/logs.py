"""Logging for operators: what the service does and how long each step takes.

Every line is `time level logger [seq] message key=value ...`. `seq` is the sequence id of the build the line
belongs to (`-` outside a build); it follows the build through asyncio tasks and worker threads, so
`grep <id>` shows one build from submission to result. Durations are in seconds (`s=`) unless the key says
otherwise.

Levels: INFO is one line per operation (an HTTP request, a build stage, an archive query); DEBUG adds one
line per exposure and every retry; WARNING marks slow or failed work. Set SPHEREX_LOG_LEVEL and
SPHEREX_LOG_FORMAT (`text` or `json`, one object per line for log tools).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import math
import time
from collections.abc import Iterator, Sequence

from .config import settings

seq_id: contextvars.ContextVar[str] = contextvars.ContextVar("seq_id", default="-")

_TEXT_FORMAT = "%(asctime)s %(levelname)-5s %(name)s [%(seq)s] %(message)s"
_configured = False


class _SeqFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.seq = seq_id.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"time": self.formatTime(record), "level": record.levelname, "logger": record.name,
               "seq": getattr(record, "seq", "-"), "message": record.getMessage(), **getattr(record, "fields", {})}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: str | None = None, fmt: str | None = None) -> None:
    """Install our handler on the root logger once; later calls only change the level. Safe to call from
    create_app(). If the root logger already has handlers (pytest, an embedding app), they are kept and only
    get the `seq` field, so their own format still works."""
    global _configured
    level = (level or settings.log_level).upper()
    root = logging.getLogger()
    for name in ("spherex_core", "spherex_api"):
        logging.getLogger(name).setLevel(level)
    if _configured:
        return
    if root.handlers:
        for h in root.handlers:
            h.addFilter(_SeqFilter())
    else:
        handler = logging.StreamHandler()
        handler.addFilter(_SeqFilter())
        handler.setFormatter(_JsonFormatter() if (fmt or settings.log_format) == "json"
                             else logging.Formatter(_TEXT_FORMAT))
        root.addHandler(handler)
        root.setLevel(logging.INFO)  # third-party libraries stay at INFO; ours follow `level`
    for name in ("asyncio", "urllib3", "aiohttp", "httpx"):  # their chatter is not ours
        logging.getLogger(name).setLevel(logging.WARNING)
    _configured = True


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 100 else f"{v:.7g}"
    s = str(v)
    return f'"{s}"' if (" " in s or not s) else s


def event(log: logging.Logger, level: int, msg: str, **fields: object) -> None:
    """Log `msg key=value ...`; the fields also go to the JSON formatter as separate keys."""
    if not log.isEnabledFor(level):
        return
    text = " ".join([msg, *(f"{k}={_fmt(v)}" for k, v in fields.items() if v is not None)])
    log.log(level, text, extra={"fields": {k: v for k, v in fields.items() if v is not None}})


@contextlib.contextmanager
def timed(log: logging.Logger, op: str, level: int = logging.INFO, slow: float | None = None,
          **fields: object) -> Iterator[dict]:
    """Time a block and log `op done s=... key=value`. The block can add fields to the yielded dict.
    A block slower than `slow` seconds logs at WARNING; a failing block logs `op failed` with the error."""
    extra: dict = {}
    t0 = time.perf_counter()
    try:
        yield extra
    except BaseException as e:
        s = time.perf_counter() - t0
        lvl = logging.INFO if isinstance(e, (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt)) else logging.WARNING
        event(log, lvl, f"{op} failed", s=s, error=f"{e.__class__.__name__}: {e}"[:300], **fields, **extra)
        raise
    s = time.perf_counter() - t0
    extra["s"] = s
    event(log, logging.WARNING if slow is not None and s > slow else level, f"{op} done", **fields, **extra)


def summary(values: Sequence[float]) -> dict:
    """p50, p90, max and sum of durations, rounded for logs and meta.json."""
    v = sorted(x for x in values if x is not None and math.isfinite(x))
    if not v:
        return {}
    pick = lambda q: v[min(len(v) - 1, int(round(q * (len(v) - 1))))]  # noqa: E731
    return {"n": len(v), "p50": round(pick(0.5), 3), "p90": round(pick(0.9), 3), "max": round(v[-1], 3),
            "sum": round(sum(v), 3)}
