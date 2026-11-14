"""The sequence pipeline: parameters, per-frame work, assembly, result format and background jobs."""

from .jobs import Job, JobManager
from .params import ALGO_VERSION, SequenceParams
from .sequence import build_sequence, cpu_pool

__all__ = ["ALGO_VERSION", "Job", "JobManager", "SequenceParams", "build_sequence", "cpu_pool"]
