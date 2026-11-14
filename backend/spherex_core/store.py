"""Shared result storage: an S3-compatible bucket (Railway Bucket, MinIO, AWS) behind the local disk cache.

Builds always write to local disk first (pipeline.results). With a bucket configured, a worker then
publishes the finished directory to `sequences/<id>/...` (meta.json last, so its presence marks a complete
object set), and any API replica that lacks a result downloads it once into its own disk cache.
Without a bucket everything stays local, which is what a laptop or the single dev server needs.

Configuration (first match wins):
    SPHEREX_BUCKET or BUCKET / AWS_S3_BUCKET_NAME     bucket name (enables the store)
    SPHEREX_BUCKET_ENDPOINT or AWS_ENDPOINT_URL        S3 endpoint URL
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY          credentials
    AWS_DEFAULT_REGION / AWS_REGION                    region (default "auto")
    SPHEREX_BUCKET_PREFIX                              key prefix (default "")
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import threading
import time
from functools import lru_cache
from pathlib import Path

from . import cache
from .pipeline import results

log = logging.getLogger(__name__)
_NEGATIVE_TTL = 5.0  # seconds to remember "not in the bucket" (status polls are frequent)


def _env(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


class ResultStore:
    def __init__(self) -> None:
        self.bucket = _env("SPHEREX_BUCKET", "BUCKET", "AWS_S3_BUCKET_NAME")
        self.prefix = _env("SPHEREX_BUCKET_PREFIX", default="") or ""
        self._client = None
        self._misses: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.bucket)

    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3", endpoint_url=_env("SPHEREX_BUCKET_ENDPOINT", "AWS_ENDPOINT_URL"),
                region_name=_env("AWS_DEFAULT_REGION", "AWS_REGION", default="auto"),
                config=Config(retries={"max_attempts": 4, "mode": "standard"}, max_pool_connections=32,
                              s3={"addressing_style": _env("SPHEREX_BUCKET_URL_STYLE", default="auto")}))
        return self._client

    def _key(self, seq_id: str, name: str) -> str:
        return f"{self.prefix}sequences/{seq_id}/{name}"

    def local_dir(self, seq_id: str) -> Path:
        return cache.layout().sequence(seq_id)

    # ------------------------------------------------------------------ read side (API replicas)
    def ensure_local(self, seq_id: str, fresh: bool = False) -> bool:
        """True when the result is complete on local disk, downloading it from the bucket if needed.
        fresh=True ignores the short negative cache (the queue just reported the build as done)."""
        d = self.local_dir(seq_id)
        if results.is_complete(d):
            return True
        if not self.enabled or (not fresh and time.monotonic() - self._misses.get(seq_id, -1e9) < _NEGATIVE_TTL):
            return False
        d.parent.mkdir(parents=True, exist_ok=True)
        # threads of this process, then other server processes sharing the disk cache (uvicorn --workers)
        with self._lock(seq_id), open(d.parent / f".{seq_id}.lock", "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            if results.is_complete(d):
                return True
            try:
                return self._download(seq_id, d)
            except Exception as e:  # noqa: BLE001 - a bucket hiccup must not break status polls
                log.warning("bucket read failed for %s: %r", seq_id, e)
                self._misses[seq_id] = time.monotonic()
                return False

    def _download(self, seq_id: str, d: Path) -> bool:
        s3 = self.client()
        resp = s3.list_objects_v2(Bucket=self.bucket, Prefix=self._key(seq_id, ""))
        names = [o["Key"].rsplit("/", 1)[-1] for o in resp.get("Contents", [])]
        if results.META not in names:
            self._misses[seq_id] = time.monotonic()
            return False
        tmp = d.with_name(d.name + ".dl")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for name in sorted(names, key=lambda n: n == results.META):  # meta.json last
            s3.download_file(self.bucket, self._key(seq_id, name), str(tmp / name))
        shutil.rmtree(d, ignore_errors=True)
        tmp.rename(d)
        self._misses.pop(seq_id, None)
        log.info("result %s fetched from bucket (%d files)", seq_id, len(names))
        return True

    # ------------------------------------------------------------------ write side (workers)
    def publish(self, seq_id: str) -> None:
        """Upload a finished local result; meta.json goes last. No-op without a bucket."""
        if not self.enabled:
            return
        d = self.local_dir(seq_id)
        files = sorted((p for p in d.iterdir() if p.is_file() and not p.name.endswith(".part")),
                       key=lambda p: p.name == results.META)
        s3 = self.client()
        for p in files:
            extra = {"ContentType": "application/json" if p.name == results.META else "application/octet-stream",
                     "CacheControl": "public, max-age=31536000, immutable"}
            s3.upload_file(str(p), self.bucket, self._key(seq_id, p.name), ExtraArgs=extra)
        self._misses.pop(seq_id, None)
        log.info("result %s published to bucket (%d files)", seq_id, len(files))

    def ping(self) -> bool:
        if not self.enabled:
            return True
        self.client().head_bucket(Bucket=self.bucket)
        return True

    def _lock(self, seq_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(seq_id, threading.Lock())


@lru_cache(maxsize=1)
def store() -> ResultStore:
    return ResultStore()
