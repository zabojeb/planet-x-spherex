"""Targets: the model with proper motion, the challenge presets, coordinate parsing and name resolution."""

from .model import Target
from .presets import PRESETS
from .resolve import parse_coordinates, resolve, resolve_name

__all__ = ["PRESETS", "Target", "parse_coordinates", "resolve", "resolve_name"]
