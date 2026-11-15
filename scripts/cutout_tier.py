"""Persistent cutout tier: keep the cached pixels of popular fields in object storage (Cloudflare R2), so a new or
wiped server serves them instantly instead of re-reading thousands of exposures from S3.

A bundle per sequence holds what the server needs to serve and analyse it without the archive: the built
result (sequences/<id>/), the per-file row chunks of its exposures (chunks/..), the legacy per-request cutouts
that point to the same files, and the per-detector wavelength / ePSF tables. Bundles are split into parts of at
most 240 MB (the wrangler upload limit is 300 MB) and described by a manifest.

    # on the server
    python scripts/cutout_tier.py bundle <seq_id ...>        # -> $SPHEREX_TIER_DIR/<seq_id>/ (parts + manifest)
    python scripts/cutout_tier.py bundle --popular           # every 'tier' entry of targets/popular.json
    python scripts/cutout_tier.py pull <seq_id ...>          # into the cache from $SPHEREX_TIER_BASE: the bucket's
                                                             # public URL, or a directory `fetch` filled
    # on a machine logged in with `wrangler login` (no credentials on the server)
    python scripts/cutout_tier.py push <seq_id ...>          # ssh-streams each part into the R2 bucket
    python scripts/cutout_tier.py fetch <seq_id ...> --base /workspace/cache/tier_in   # R2 -> server, no public URL

Env: SPHEREX_TIER_BUCKET (default planet-x-spherex-tier), SPHEREX_TIER_BASE (public base URL of the bucket, if it
has one), SPHEREX_TIER_DIR (default <cache>/tier). push/fetch read DEPLOY_HOST/DEPLOY_PORT/DEPLOY_KEY from
.env.deploy.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

PART_BYTES = 240 * 1024 * 1024
BUCKET = os.environ.get("SPHEREX_TIER_BUCKET", "planet-x-spherex-tier")


def _cache_root() -> Path:
    from spherex_core.config import settings

    return Path(settings.cache_dir)


def _tier_dir() -> Path:
    return Path(os.environ.get("SPHEREX_TIER_DIR") or (_cache_root() / "tier"))


def files_of(seq_id: str) -> list[Path]:
    """Every cache file a sequence needs, relative paths under the cache root."""
    from spherex_core import cache
    from spherex_core.analysis.common import load_context, sequence_frames

    root = _cache_root()
    lay = cache.layout()
    seq_dir = lay.sequence(seq_id)
    out = [p for p in seq_dir.rglob("*") if p.is_file()]
    ctx = load_context(seq_id)
    frames = [f for f in sequence_frames(ctx) if f is not None]
    for f in frames:
        p = lay.chunk(f.s3_url)
        if p.exists():
            out.append(p)
    # legacy per-request cutouts (written before ADR-0037 chunks, turned into chunks when next read): keyed by
    # url + grid centre + half-size, the half-size the old builds used for this grid
    import math

    g = ctx.meta["grid"]
    half = int(math.ceil(g["n"] * g["pixscale_arcsec"] / 6.15 * 0.5 * 1.42)) + 3
    for f in frames:
        p = lay.cutout(f.s3_url, g["ra"], g["dec"], half)
        if p.exists():
            out.append(p)
    for d in range(1, 7):
        for p in (lay.wave(d), lay.epsf(d)):
            if p.exists():
                out.append(p)
    return sorted(set(out))


def bundle(seq_id: str) -> dict:
    root, outdir = _cache_root(), _tier_dir() / seq_id
    outdir.mkdir(parents=True, exist_ok=True)
    files = files_of(seq_id)
    parts, buf, part_files, total = [], None, [], 0

    def flush():
        nonlocal buf
        if buf is None:
            return
        buf.close()
        data = raw.getvalue()
        name = f"part{len(parts):03d}.tar"
        (outdir / name).write_bytes(data)
        parts.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "files": len(part_files)})
        buf = None

    for p in files:
        size = p.stat().st_size
        if buf is None:
            raw = io.BytesIO()
            buf = tarfile.open(fileobj=raw, mode="w")
            part_files = []
        buf.add(p, arcname=str(p.relative_to(root)))
        part_files.append(p)
        total += size
        if raw.tell() >= PART_BYTES:
            flush()
    flush()
    manifest = {"sequence": seq_id, "files": len(files), "bytes": total, "parts": parts}
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def _ssh_args() -> list[str]:
    env = {}
    for line in Path(".env.deploy").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = os.path.expandvars(v.strip())
    return ["ssh", "-i", env.get("DEPLOY_KEY", "~/.ssh/id_ed25519"), "-o", "IdentitiesOnly=yes",
            "-p", env.get("DEPLOY_PORT", "22"), env["DEPLOY_HOST"]]


def push(seq_id: str, remote_tier: str, skip: int = 0) -> dict:
    """Stream each part from the server into R2 with wrangler (uses this machine's `wrangler login`)."""
    ssh = _ssh_args()
    manifest = json.loads(subprocess.run(ssh + [f"cat {remote_tier}/{seq_id}/manifest.json"], check=True,
                                         capture_output=True).stdout)
    names = [p["name"] for p in manifest["parts"]] + ["manifest.json"]  # the manifest last: it marks completion
    for name in names[skip:]:
        for attempt in range(3):  # a 240 MB put through a home connection does fail now and then
            src = subprocess.Popen(ssh + [f"cat {remote_tier}/{seq_id}/{name}"], stdout=subprocess.PIPE)
            r = subprocess.run(["wrangler", "r2", "object", "put", f"{BUCKET}/tier/{seq_id}/{name}", "--pipe",
                                "--remote"], stdin=src.stdout, capture_output=True, text=True)
            src.wait()
            if r.returncode == 0:
                break
            print(f"  {seq_id}/{name} attempt {attempt + 1} failed: {(r.stderr or r.stdout).strip()[-300:]}",
                  flush=True)
        else:
            raise RuntimeError(f"could not push {seq_id}/{name}")
        print(f"  pushed {seq_id}/{name}", flush=True)
    return manifest


def _read(base: str, rel: str) -> bytes:
    """`rel` under a public bucket URL (http/https) or a local directory laid out like the bucket."""
    if base.startswith(("http://", "https://")):
        with urllib.request.urlopen(f"{base.rstrip('/')}/{rel}", timeout=600) as r:
            return r.read()
    return (Path(base) / rel).read_bytes()


def fetch(seq_id: str, remote_dir: str) -> dict:
    """Copy a bundle from R2 to the server's `remote_dir` (laid out like the bucket) through this machine's
    `wrangler login`, for when the bucket has no public URL; then run `pull <id> --base <remote_dir>` there."""
    ssh = _ssh_args()

    def get(name: str) -> None:
        src = subprocess.Popen(["wrangler", "r2", "object", "get", f"{BUCKET}/tier/{seq_id}/{name}", "--pipe",
                                "--remote"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        subprocess.run(ssh + [f"mkdir -p {remote_dir}/tier/{seq_id} && cat > {remote_dir}/tier/{seq_id}/{name}"],
                       stdin=src.stdout, check=True)
        if src.wait() != 0:
            raise RuntimeError(f"wrangler could not read {seq_id}/{name}")

    get("manifest.json")
    manifest = json.loads(subprocess.run(ssh + [f"cat {remote_dir}/tier/{seq_id}/manifest.json"], check=True,
                                         capture_output=True).stdout)
    for part in manifest["parts"]:
        get(part["name"])
        print(f"  fetched {seq_id}/{part['name']}", flush=True)
    return manifest


def pull(seq_id: str, base: str) -> dict:
    """Unpack a bundle into the cache from the bucket's public URL or a local copy (existing files are kept;
    each part's checksum is verified before it is unpacked)."""
    root = _cache_root()
    manifest = json.loads(_read(base, f"tier/{seq_id}/manifest.json"))
    added = 0
    for part in manifest["parts"]:
        data = _read(base, f"tier/{seq_id}/{part['name']}")
        if hashlib.sha256(data).hexdigest() != part["sha256"]:
            raise RuntimeError(f"checksum mismatch in {seq_id}/{part['name']}")
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for m in tar.getmembers():
                dest = (root / m.name).resolve()
                if not str(dest).startswith(str(root.resolve())) or not m.isfile():
                    continue  # never write outside the cache
                if dest.exists():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as fh:
                    dest.write_bytes(fh.read())
                added += 1
    return {"sequence": seq_id, "files_added": added, "bytes": manifest["bytes"]}


def popular_sequences() -> list[str]:
    """Sequence ids of the 'tier' entries of popular.json that are already built (same params as prewarm)."""
    from spherex_core import popular
    from spherex_core.pipeline.params import SequenceParams
    from spherex_core.targets import resolve

    out = []
    for e in popular.curated():
        if not e.get("tier") or e.get("moving"):
            continue
        t = resolve(e["query"])
        p = SequenceParams(ra=t.ra, dec=t.dec, pmra=t.pmra, pmdec=t.pmdec, ref_epoch=t.ref_epoch, name=t.name,
                           fov_arcmin=float(e.get("fov", 6)))
        if (_cache_root() / "sequences" / p.key() / "meta.json").exists():
            out.append(p.key())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["bundle", "push", "fetch", "pull", "list"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--popular", action="store_true")
    ap.add_argument("--remote-tier", default="/workspace/cache/tier")
    ap.add_argument("--base", default=os.environ.get("SPHEREX_TIER_BASE", ""))
    ap.add_argument("--skip-parts", type=int, default=0, help="push: resume after this many parts of one sequence")
    a = ap.parse_args()
    ids = a.ids
    if a.popular and a.cmd in ("bundle", "list"):
        ids = ids + popular_sequences()
    if a.cmd == "list":
        print("\n".join(ids))
        return
    total = 0
    for sid in ids:
        if a.cmd == "bundle":
            m = bundle(sid)
        elif a.cmd == "push":
            m = push(sid, a.remote_tier, a.skip_parts)
        elif a.cmd == "fetch":
            m = fetch(sid, a.base or "/workspace/cache/tier_in")
        else:
            if not a.base:
                sys.exit("set SPHEREX_TIER_BASE or --base to the bucket's public URL or a fetched copy")
            m = pull(sid, a.base)
        total += m.get("bytes", 0)
        print(json.dumps({k: v for k, v in m.items() if k != "parts"}), flush=True)
    print(json.dumps({"sequences": len(ids), "bytes": total, "gb": round(total / 1e9, 3)}))


if __name__ == "__main__":
    main()
