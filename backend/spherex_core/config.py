"""Runtime settings. Every value can be overridden with an environment variable SPHEREX_<NAME>."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Searched by default, in order of preference when the same exposure is in more than one release.
DEFAULT_COLLECTIONS = "spherex_dr1,spherex_dr1_deep,spherex_qr3,spherex_qr3_deep"


def _env(name: str, default: str) -> str:
    return os.environ.get(f"SPHEREX_{name}", default)


@dataclass(frozen=True)
class Settings:
    tap_url: str = _env("TAP_URL", "https://irsa.ipac.caltech.edu/TAP/sync")
    s3_base: str = _env("S3_BASE", "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/")
    irsa_base: str = _env("IRSA_BASE", "https://irsa.ipac.caltech.edu/")
    gaia_tap_url: str = _env("GAIA_TAP_URL", "https://gea.esac.esa.int/tap-server/tap/sync")
    sesame_url: str = _env("SESAME_URL", "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/~SNV")
    collections: tuple[str, ...] = tuple(
        c.strip() for c in _env("COLLECTIONS", DEFAULT_COLLECTIONS).split(",") if c.strip()
    )
    cache_dir: Path = Path(_env("CACHE_DIR", str(Path.home() / ".cache" / "spherex")))
    max_connections: int = int(_env("MAX_CONNECTIONS", "96"))
    max_frames_in_flight: int = int(_env("MAX_FRAMES_IN_FLIGHT", "64"))
    cpu_workers: int = int(_env("CPU_WORKERS", "24"))
    tap_cache_hours: float = float(_env("TAP_CACHE_HOURS", "12"))


settings = Settings()
