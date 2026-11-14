"""Process-level runtime helpers: CPU quota detection and structured logging.

Containers (Railway, vast.ai, Docker) report the host's cores through os.cpu_count(); the usable
share is the cgroup quota. Pools and server workers are sized from cpu_quota().
"""

from __future__ import annotations

import math
import os
from pathlib import Path


def cpu_quota() -> int:
    """Usable CPUs: cgroup v2 cpu.max or v1 cfs quota, else the affinity mask, else os.cpu_count()."""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return max(1, math.floor(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:
        q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if q > 0 and p > 0:
            return max(1, math.floor(q / p))
    except (OSError, ValueError):
        pass
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def setup_logging() -> None:
    """Operator logging (spherex_core.logs): `[seq]` on every line, text or JSON. Kept as the entry point used by
    the API factory and the worker."""
    from . import logs

    logs.configure()
