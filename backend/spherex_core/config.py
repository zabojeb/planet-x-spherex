"""Runtime settings. Every value can be overridden with an environment variable SPHEREX_<NAME>."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .runtime import cpu_quota

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
    # SIMBAD TAP services tried in order (comma-separated): CDS, then the CfA mirror.
    simbad_tap_urls: tuple[str, ...] = tuple(u.strip() for u in _env(
        "SIMBAD_TAP_URLS",
        "https://simbad.cds.unistra.fr/simbad/sim-tap/sync,https://simbad.harvard.edu/simbad/sim-tap/sync",
    ).split(",") if u.strip())
    collections: tuple[str, ...] = tuple(
        c.strip() for c in _env("COLLECTIONS", DEFAULT_COLLECTIONS).split(",") if c.strip()
    )
    cache_dir: Path = Path(_env("CACHE_DIR", str(Path.home() / ".cache" / "spherex")))
    max_connections: int = int(_env("MAX_CONNECTIONS", "96"))
    max_frames_in_flight: int = int(_env("MAX_FRAMES_IN_FLIGHT", "64"))
    # Cached rows of a Level 2 file keep enough columns for this field of view (arcmin), so widening the field up
    # to it reads only the new rows (ADR-0037). Rows arrive full-width anyway; this costs disk, not network.
    cutout_cache_fov: float = float(_env("CUTOUT_CACHE_FOV", "30"))
    # Where cutouts come from: "irsa" asks IRSA's cutout service first (one request, ~160 KB at 6') and reads from S3
    # when it is busy or fails; "s3" reads byte ranges from S3 only (ADR-0038).
    cutout_source: str = _env("CUTOUT_SOURCE", "irsa")
    irsa_max_in_flight: int = int(_env("IRSA_IN_FLIGHT", "8"))  # the service answers 503 to most requests above ~8
    irsa_timeout_s: float = float(_env("IRSA_TIMEOUT", "15"))  # then the exposure is read from S3
    # Read and process exposures in batches in the process pool, every core at work (ADR-0039); 0 keeps everything
    # in the API's event loop (one core), as before.
    fetch_workers: bool = _env("FETCH_WORKERS", "1") not in ("0", "false", "no")
    cpu_workers: int = int(_env("CPU_WORKERS", str(max(1, cpu_quota() - 1))))  # process pool size
    max_jobs: int = int(_env("MAX_JOBS", "3"))  # sequence builds running at once; the rest queue
    job_timeout_s: float = float(_env("JOB_TIMEOUT", "900"))  # stop a build after this long (0: never)
    job_idle_s: float = float(_env("JOB_IDLE", "120"))  # cancel a build no client asked about for this long (0: never)
    job_detach_grace_s: float = float(_env("JOB_DETACH_GRACE", "5"))  # wait after the last client left
    log_level: str = _env("LOG_LEVEL", "INFO")  # DEBUG adds a line per exposure, retry and progress poll
    log_format: str = _env("LOG_FORMAT", "json" if os.environ.get("SPHEREX_LOG_JSON") == "1" else "text")  # text | json
    slow_frame_s: float = float(_env("SLOW_FRAME", "30"))  # log an exposure slower than this as WARNING
    slow_request_s: float = float(_env("SLOW_REQUEST", "5"))  # log an HTTP request slower than this as WARNING
    tap_cache_hours: float = float(_env("TAP_CACHE_HOURS", "12"))


settings = Settings()
