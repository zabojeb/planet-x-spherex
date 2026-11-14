"""Downloads of a built sequence: FITS co-adds, photometry tables, a script for the original files."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from spherex_core import services
from spherex_core.errors import NotFound, UpstreamError

router = APIRouter(prefix="/api")


def _attachment(data: bytes, media: str, filename: str) -> Response:
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/sequences/{seq_id}/export/coadds.fits")
def coadds_fits(seq_id: str, epoch: int | None = Query(None, ge=0), band: str | None = None,
                coverage: bool = False) -> Response:
    """One co-add (epoch + band) or all of them as a multi-extension FITS (EXTNAME E<epoch>_<band>)."""
    try:
        data = services.export_coadds_fits(seq_id, epoch, band, coverage)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    name = f"spherex_{seq_id}" + (f"_E{epoch}_{band}" if epoch is not None else "") + ".fits"
    return _attachment(data, "application/fits", name)


@router.get("/sequences/{seq_id}/export/photometry.{fmt}")
def photometry(seq_id: str, fmt: Literal["csv", "vot"]) -> Response:
    try:
        data = services.export_photometry(seq_id, fmt)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    media = "text/csv" if fmt == "csv" else "application/x-votable+xml"
    return _attachment(data, media, f"spherex_{seq_id}_photometry.{'csv' if fmt == 'csv' else 'xml'}")


@router.get("/sequences/{seq_id}/export/download.sh")
def download_sh(seq_id: str, kind: Literal["full", "cutout"] = "full",
                source: Literal["irsa", "s3"] = "irsa") -> Response:
    """Shell script with one curl per original Level 2 file (whole files or IRSA cutouts of the field)."""
    try:
        text = services.export_download_script(seq_id, kind, source)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except UpstreamError as e:
        raise HTTPException(502, str(e)) from e
    return _attachment(text.encode(), "text/x-shellscript", f"spherex_{seq_id}_{kind}.sh")
