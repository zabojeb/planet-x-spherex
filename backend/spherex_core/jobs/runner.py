"""Run one sequence build under supervision: cancellation checks, maximum runtime, cleanup, publishing."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Awaitable, Callable

from .. import cache
from ..pipeline.params import SequenceParams
from ..pipeline.sequence import build_sequence
from ..store import store
from .model import JobCancelled, queue_settings

log = logging.getLogger(__name__)
Progress = Callable[[str, int, int], None]
CancelCheck = Callable[[], Awaitable[str | None]]  # returns a reason when the build should stop
BuildFn = Callable[[SequenceParams, "object", Progress], Awaitable[object]]
CHECK_INTERVAL = 1.0


async def _publish_preview(preview_id: str) -> None:
    """With a shared bucket, API replicas other than this worker can only serve a preview from there."""
    if store().enabled:
        await asyncio.to_thread(store().publish, preview_id)


async def _default_build(params: SequenceParams, out_dir, progress: Progress) -> object:
    shown: list[str] = []

    async def on_preview(preview_id: str) -> None:
        shown.append(preview_id)
        await _publish_preview(preview_id)

    meta = await build_sequence(params, out_dir, progress, max_frames=queue_settings().max_frames,
                                on_preview=on_preview)
    if shown and store().enabled:  # the full result replaces them (build_sequence removed the local copies)
        await asyncio.to_thread(lambda: [store().remove(pid) for pid in shown])
    return meta


async def run_build(job_id: str, params: SequenceParams, progress: Progress, should_cancel: CancelCheck,
                    build: BuildFn | None = None, max_runtime: float | None = None) -> None:
    """Build, then publish to the shared store. Raises JobCancelled, TimeoutError or the build's error.

    The build runs as a task; every CHECK_INTERVAL seconds `should_cancel` is asked whether anyone still
    wants the result. Cancelling the task propagates into build_sequence: pending aiohttp range requests
    are aborted and process-pool futures that have not started are cancelled.
    """
    out_dir = cache.layout().sequence(job_id)
    max_runtime = max_runtime or queue_settings().max_runtime
    task = asyncio.create_task((build or _default_build)(params, out_dir, progress))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_runtime
    reason: str | None = None
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=CHECK_INTERVAL)
            if done:
                break
            if loop.time() > deadline:
                reason = f"took longer than {max_runtime:.0f} s"
            else:
                reason = await should_cancel()
            if reason:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                _cleanup(out_dir)
                if reason.startswith("took longer"):
                    raise TimeoutError(f"The build {reason} and was stopped.")
                raise JobCancelled(reason)
        task.result()
    except asyncio.CancelledError:  # the job runner itself is shutting down
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        _cleanup(out_dir)
        raise
    await asyncio.to_thread(store().publish, job_id)


def _cleanup(out_dir) -> None:
    shutil.rmtree(out_dir.with_name(out_dir.name + ".tmp"), ignore_errors=True)
    log.info("build %s stopped; temporary files removed", out_dir.name)
