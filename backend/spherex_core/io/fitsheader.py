"""FITS header helpers for reading files piecewise: header parsing, HDU sizes, slim WCS headers."""

from __future__ import annotations

import re

from astropy.io import fits

BLOCK = 2880
CARD = 80


def padded(n: int) -> int:
    """n rounded up to whole 2880-byte FITS blocks."""
    return -(-n // BLOCK) * BLOCK


def parse_header(buf: bytes, start: int = 0) -> tuple[fits.Header, int] | None:
    """Parse a FITS header that starts at buf[start]. Returns (header, padded length) or None if END is missing."""
    pos, n = start, len(buf)
    while pos + CARD <= n:
        card = buf[pos:pos + CARD]
        if card[:3] == b"END" and not card[3:].strip():
            end = pos + CARD
            return fits.Header.fromstring(buf[start:end].decode("ascii", "replace")), padded(end - start)
        pos += CARD
    return None


def data_nbytes(h: fits.Header) -> int:
    """Size of an HDU's data unit in bytes, before padding (images and binary tables with heap)."""
    naxis = int(h.get("NAXIS", 0))
    if naxis == 0:
        return 0
    n = 1
    for i in range(1, naxis + 1):
        n *= int(h[f"NAXIS{i}"])
    return abs(int(h["BITPIX"])) // 8 * int(h.get("GCOUNT", 1)) * (int(h.get("PCOUNT", 0)) + n)


_WCS_KEY = re.compile(r"^((CTYPE|CRPIX|CRVAL|CDELT|CUNIT)[12]|(PC|CD)[12]_[12]|(A|B|AP|BP)_(\d+_\d+|ORDER|DMAX)"
                      r"|LONPOLE|LATPOLE|RADESYS|EQUINOX|WCSAXES|NAXIS[12]?)$")
_META_KEYS = ("EXTNAME", "DETECTOR", "OBSID", "MJD-AVG", "MJD-BEG", "MJD-END", "DATE-AVG", "PSF_FWHM",
              "OMEGA_MEDIAN", "XPOSURE", "BUNIT")


def slim_header(h: fits.Header) -> fits.Header:
    """Only the celestial WCS (with SIP) and the metadata the pipeline uses. Building a WCS from the full
    ~300-card IMAGE header costs ~35 ms; from this one it is a few ms."""
    return fits.Header([(k, h[k]) for k in h if _WCS_KEY.match(k) or k in _META_KEYS])
