"""A POSIX shell script that downloads the original Level 2 files of a sequence."""

from __future__ import annotations

from ..archive import Frame

FILE_MB = 64


def download_script(meta: dict, frames: list[Frame | None], kind: str = "full", source: str = "irsa") -> str:
    """kind: 'full' (whole files, ~64 MB each) or 'cutout' (IRSA cutout service, the sequence's field).
    source: 'irsa' or 's3' (anonymous HTTPS from the public bucket, us-east-1); cutouts are IRSA only."""
    if kind not in ("full", "cutout") or source not in ("irsa", "s3"):
        raise ValueError("kind must be full|cutout and source irsa|s3")
    fs = [f for f in frames if f is not None]
    g = meta["grid"]
    size_deg = g["fov_arcmin"] / 60.0 * 1.42
    name = meta["target"].get("name") or f"{g['ra']:.5f} {g['dec']:+.5f}"
    lines = [
        "#!/bin/sh",
        f"# SPHEREx Level 2 files of sequence {meta['id']} ({name})",
        f"# {len(fs)} files" + (f", about {len(fs) * FILE_MB / 1024:.1f} GB" if kind == "full" else
                                f", IRSA cutouts of {size_deg:.3f} deg"),
        "# Run: sh this_script.sh   (needs curl)",
        "set -eu",
        f"mkdir -p spherex_{meta['id']} && cd spherex_{meta['id']}",
    ]
    for f in fs:
        if kind == "cutout":
            url = f"{f.irsa_url}?center={g['ra']:.6f},{g['dec']:.6f}d&size={size_deg:.4f}"
            out = f.file_name.replace(".fits", "_cutout.fits")
        else:
            url = f.s3_url if source == "s3" else f.irsa_url
            out = f.file_name
        lines.append(f'[ -s "{out}" ] || curl -fL --retry 3 -o "{out}" "{url}"')
    lines.append(f'echo "done: {len(fs)} files in spherex_{meta["id"]}"')
    return "\n".join(lines) + "\n"
