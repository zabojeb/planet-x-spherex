"""The sequence pipeline: parameters, per-frame work, assembly and the result format.

Background execution (queues, workers, cancellation) lives in spherex_core.jobs.
"""

from .params import ALGO_VERSION, SequenceParams
from .sequence import build_sequence, cpu_pool

__all__ = ["ALGO_VERSION", "SequenceParams", "build_sequence", "cpu_pool"]
