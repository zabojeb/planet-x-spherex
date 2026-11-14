# HTTP API

Reference for the API in `backend/spherex_api` as of 2026-11-14. Interactive OpenAPI docs are served by FastAPI at `/docs` on any running instance. Examples use `http://<api-host>`; replace it with your server (see [development.md](development.md)).

General behaviour:

- JSON everywhere except the `.bin` endpoints, which return raw little-endian arrays (`application/octet-stream`).
- CORS is open to all origins, so the viewer can be embedded and called from other sites.
- Responses larger than 2 KB are gzip-compressed when the client accepts it.
- When `web/dist` exists, the built web app is served at `/`.

## Health

`GET /api/health`

```json
{"ok": true, "version": "0.1.0", "collections": ["spherex_dr1", "spherex_dr1_deep", "spherex_qr3", "spherex_qr3_deep"]}
```

## Targets

`GET /api/targets` returns the five challenge presets. Each target:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `barnard`, `eps-indi-b`, `wise-0855`, `rw-aur`, `r-dra` |
| `name`, `description`, `source` | string | display name, one-line description, where the values come from |
| `ra`, `dec` | float | degrees, ICRS, at `ref_epoch` |
| `pmra`, `pmdec` | float | mas/yr (`pmra` includes cos δ) |
| `ref_epoch` | float | Julian year of the position |
| `parallax` | float or null | mas |
| `fov_arcmin` | float or null | suggested field (16′ for ε Indi B so the star ε Indi A is in view, 6′ otherwise) |
| `default_band` | string or null | detector a preset opens with: `all` or `D1`–`D6` (WISE 0855: `D6`, RW Aur: `D6`, R Dra: `D1`, others `all`) |
| `default_mode` | string or null | view a preset opens with: `blink`, `diff`, `stack`, `rgb` or `exp` (Barnard's Star: `diff`, WISE 0855: `rgb`, others `blink`) |

`GET /api/resolve?q=<text>` resolves a preset id or name, coordinates (`269.452 4.693`, `17:57:48.5 +04:41:36`, `17h57m48.5s 4d41m36s`) or an object name through CDS Sesame. Returns a target as above (`pmra`/`pmdec` = 0 when unknown). `404` if the name is unknown or the resolver is unreachable.

```bash
curl "http://<api-host>/api/resolve?q=RW%20Aur"
```

## Sequences

### Create or reuse

`POST /api/sequences` with a JSON body:

| Field | Default | Constraints | Meaning |
|---|---|---|---|
| `ra` | — | 0 ≤ ra < 360 | degrees at `ref_epoch` |
| `dec` | — | −90 ≤ dec ≤ 90 | degrees |
| `pmra`, `pmdec` | 0 | | mas/yr |
| `ref_epoch` | 2000.0 | | Julian year |
| `name` | null | | display name, not part of the cache key |
| `fov_arcmin` | 6.0 | 1–30 | field of view |
| `gap_days` | 20 | > 0, ≤ 365 | gap that starts a new epoch |
| `max_epoch_days` | 45 | > 0, ≤ 400 | maximum epoch length |
| `force` | false | | rebuild even if a result exists |

The response is the job:

```json
{"id": "48006f61db3e73e6ae51", "status": "running", "stage": "fetch", "done": 120, "total": 358, "error": null, "elapsed": 9.4}
```

`status` is `queued`, `running`, `done` or `error`; `stage` is `search`, `fetch` (with `done`/`total` exposures), `stack` or `done`. The `id` is a hash of the normalised parameters, so identical requests share one job and one result.

```bash
curl -X POST "http://<api-host>/api/sequences" -H "Content-Type: application/json" \
  -d '{"ra": 269.4520769586, "dec": 4.6933649666, "pmra": -801.551, "pmdec": 10362.394, "ref_epoch": 2000, "name": "Barnard'\''s Star", "fov_arcmin": 6}'
```

### Poll

`GET /api/sequences/{id}`

| Status code | Body |
|---|---|
| `200` | the finished `meta.json` |
| `202` | the job (still queued or running) |
| `500` | the job, with `error` set |
| `404` | unknown id |

### `meta.json`

| Key | Content |
|---|---|
| `id`, `version`, `created`, `params` | sequence id, algorithm version, creation time (UTC), request parameters |
| `target` | the target plus `track`: predicted position per epoch, `{epoch, ra, dec, x, y}` with `x`, `y` in grid pixels |
| `grid` | `ra`, `dec` (centre), `n`, `pixscale_arcsec`, `fov_arcmin`, `projection` (`TAN`), `orientation` (north up, east left) |
| `bands` | `[{id, wl_min, wl_max, R}]` for D1–D6 |
| `epochs` | `[{index, n, mjd_mean, mjd_min, mjd_max, date_min, date_max, bands: {D1: {n, wl_min, wl_max, mjd_mean, noise}, …}}]` |
| `arrays` | file, dtype and shape of `coadds`, `coverage`, `flags`, `exposures` (`exposures` is null when too large) |
| `exposures` | one entry per exposure in array order: `index, epoch, band, mjd, date, wl, dwl, rms, psf_fwhm, obs_id, collection, file, flag_counts, target_px` |
| `photometry` | one entry per measured exposure: `index, epoch, band, mjd, wl, dwl, flux_mjy, err_mjy, ab_mag, flagged, ap_flags, masked_frac, method, saturated, npix, chi2, psf_weight, flux_ap_mjy, err_ap_mjy, relative, ring_r_in, ring_r_out`. `method` is `psf` (ePSF fit), `aperture` (fallback) or `ring` (detector where the target saturates). Ring entries have `relative: true` and `ab_mag: null`: their flux is the ring's share of the light, comparable only within one detector and wavelength (divide by the median of the wavelength bin for a light curve), so do not plot them on an absolute spectrum |
| `processing` | pipeline constants, and `rings`: per detector that uses rings, `{saturated_share, core_radius_px, r_in, r_out, exposures}` |
| `flag_bits` | `[{bit, name, description, masked, phot_bad}]` read from the files |
| `stats` | `frames_found`, `frames_used`, `errors`, `requests`, `bytes`, `cache_hits`, `seconds` (`search`, `fetch_and_align`, `total`) |
| `sources` | archive, collections and data location |

Wavelengths (`wl`, `dwl`) are in µm, times in MJD, fluxes in mJy. Missing values are `null`.

### Arrays

`GET /api/sequences/{id}/coadds.bin` · `coverage.bin` · `flags.bin`

| Array | dtype | Shape | Meaning |
|---|---|---|---|
| `coadds` | float32 | `[epochs, 6, n, n]` | background-subtracted co-adds, MJy/sr; NaN = no data |
| `coverage` | uint16 | `[epochs, 6, n, n]` | exposures contributing to each pixel |
| `flags` | uint32 | `[epochs, 6, n, n]` | OR of the exposures' flag bits |

Row-major order with the detector axis in `bands` order (D1…D6). Within an image, the last axis is x and increases towards the west, the second-to-last is y and increases towards the north, so element `[0, 0]` is the south-east corner. To draw north up and east left on a canvas, whose row 0 is at the top, flip the rows.

`GET /api/sequences/{id}/exposures.bin?epoch=<i>&band=<D1..D6>` returns the aligned single exposures of one epoch and detector as float32 `[k, n, n]`. The response header `X-Exposure-Indices` lists their indices in `meta.exposures`. `404` if single exposures were not stored (very large fields) or the epoch has no exposures in that detector.

```bash
curl -o coadds.bin "http://<api-host>/api/sequences/48006f61db3e73e6ae51/coadds.bin"
python -c "import numpy as np, json, urllib.request as u; m=json.load(u.urlopen('http://<api-host>/api/sequences/48006f61db3e73e6ae51')); print(np.fromfile('coadds.bin','<f4').reshape(m['arrays']['coadds']['shape']).shape)"
```

## Catalogues

`GET /api/catalogs` lists the available catalogues: `gaia` (Gaia DR3, ESA), `catwise` (CatWISE2020, IRSA), `2mass` (2MASS point sources, IRSA).

`GET /api/catalogs/{name}?ra=<deg>&dec=<deg>&radius_arcmin=<r>` (radius default 5, maximum 30) returns up to 3,000 sources:

```json
[{"id": "Gaia DR3 4472832130942575872", "ra": 269.4485, "dec": 4.7394, "epoch": 2016.0,
  "pmra": -801.55, "pmdec": 10362.39, "parallax": 546.98, "mag": 8.19, "mag_band": "G", "color": 2.83}]
```

Every source has `epoch` (Julian year of `ra`/`dec`) and `pmra`/`pmdec` in mas/yr (null when unknown), so clients can place it at any date. `502` with a readable `detail` (naming the catalogue) if the upstream service fails, times out or returns something unusable; the client can simply retry. The example values above are illustrative, not a recorded response.

## Analyses of a built sequence

All return `404 {"detail": "sequence not ready"}` for an unknown or unfinished id and `422` for bad parameters.

### `POST /api/sequences/{id}/photometry`

Forced photometry of any position (optionally moving) in every exposure, on the sequence's cached cutouts (ADR-0025). Synchronous, a few seconds. Body: `{"ra": deg, "dec": deg, "pmra": 0, "pmdec": 0, "ref_epoch": 2000.0, "name": null}` (`pmra` includes cos dec, mas/yr; `ref_epoch` is the Julian year of `ra`/`dec`, e.g. 2016.0 for Gaia DR3).

```json
{"sequence": "<id>", "target": {"ra": ..., "dec": ..., "pmra": ..., ...},
 "photometry": [ /* records exactly like meta.json photometry: index, epoch, band, mjd, wl, dwl, flux_mjy,
                    err_mjy, ab_mag, flagged, ap_flags, masked_frac, method, saturated, npix, chi2, psf_weight,
                    flux_ap_mjy, err_ap_mjy, relative, ring_r_in, ring_r_out */ ],
 "rings": {"D1": {"r_in": 9, "r_out": 12, ...}},
 "stats": {"exposures": 358, "measured": 357, "missing_files": 0, "bytes_downloaded": 0, "seconds": 3.1}}
```

`502` if the sequence's files cannot be read.

### `GET /api/sequences/{id}/motion`

Proper motion from per-epoch co-add centroids (ADR-0026). Query: `x`,`y` (grid pixels, 0-based) or `ra`,`dec`; optional `bands=D1,D2`, `pmra`,`pmdec` (prior motion used to predict where to look).

```json
{"start": {"x": 29.0, "y": 29.0, "ra": ..., "dec": ...}, "reference_year": 2026.15, "prior": null,
 "combined": {"pmra": 3628, "pmdec": -2526, "pmra_err": 305, "pmdec_err": 260, "pm_total": 4421,
              "pm_total_err": 290, "pa_deg": 124.8, "chi2_red_ra": 30.9, "chi2_red_dec": 22.4,
              "n_points": 22, "n_epochs": 4, "bands": ["D1", ...]},
 "per_band": {"D1": { /* same fields */ }},
 "points": [{"epoch": 0, "band": "D1", "mjd": ..., "year": ..., "x": ..., "y": ..., "ra": ..., "dec": ...,
             "dra_arcsec": ..., "ddec_arcsec": ..., "sigma_arcsec": 0.12, "snr": 2969,
             "resid_ra_arcsec": ..., "resid_dec_arcsec": ...}],
 "track": [{"epoch": 0, "year": ..., "ra": ..., "dec": ..., "x": ..., "y": ...}]}
```

Motions in mas/yr (RA includes cos dec), `pa_deg` east of north. `422` if the source is found in fewer than two epochs.

### `GET /api/sequences/{id}/periodogram` · `POST /api/periodogram`

Lomb–Scargle of the target's light curve, with fluxes normalised per wavelength bin exactly as the web client does (ADR-0027). Query (GET) or body fields (POST, plus `photometry`: records like meta.json's): `method=standard|multiband`, `band`, `min_period` (days, default 0.5), `max_period` (default the baseline), `include_flagged=false`.

```json
{"method": "standard", "band": null, "n_points": 2452, "bands": ["D1", ...], "include_flagged": false,
 "relative_points": 2452, "baseline_days": 520.3, "min_period_days": 20, "max_period_days": 520.3,
 "best": {"period_days": 235.7, "frequency_per_day": 0.00424, "power": 0.46, "false_alarm_probability": 8e-321},
 "peaks": [{"period_days": 235.7, "power": 0.46}, ...],
 "periodogram": {"period_days": [...], "power": [...]},
 "t0_mjd": 60789.8,
 "folded": [{"index": 12, "mjd": ..., "phase": 0.31, "rel": 1.12, "rel_err": 0.03, "band": "D1", "wl": 0.92,
             "flagged": false, "relative": true}],
 "model": {"phase": [0, ...200 values], "rel": [...]}}
```

For `multiband`, `false_alarm_probability` is null and the model is `{"phase": [...], "rel_by_band": {"D1": [...], ...}}`. `422` with fewer than 6 usable points.

## Exports

| Request | Returns |
|---|---|
| `GET /api/sequences/{id}/export/coadds.fits` | All co-adds as one multi-extension FITS (`EXTNAME E<epoch>_<band>`); `?epoch=0&band=D6` for one image; `&coverage=true` adds coverage images |
| `GET /api/sequences/{id}/export/photometry.csv` | Photometry with exposure provenance (CSV) |
| `GET /api/sequences/{id}/export/photometry.vot` | The same as a VOTable with units |
| `GET /api/sequences/{id}/export/download.sh` | Shell script with one `curl` per original Level 2 file; `?source=s3` for the public bucket, `?kind=cutout` for IRSA cutouts of the field |

All are sent with `Content-Disposition: attachment`. FITS headers carry the grid WCS, `BUNIT = MJy/sr`, dates, wavelength range, frame count and provenance (ADR-0028).

## Data freshness

### `GET /api/releases`

```json
{"checked": "2026-11-14T12:00:00+00:00", "searched_collections": ["spherex_dr1", ...],
 "collections": [{"collection": "spherex_qr3", "images": 362124, "oldest_mjd": 61174.5, "newest_mjd": 61318.0,
                  "oldest_date": "2026-05-14 12:08", "newest_date": "2026-10-05 00:01", "searched": true}, ...]}
```

Cached for an hour (the TAP aggregate takes 14–29 s at IRSA). `502` if IRSA TAP fails.

### `GET /api/sequences/{id}/freshness`

```json
{"sequence": "<id>", "built": "...", "checked": "...", "exposures_in_sequence": 358, "exposures_available": 358,
 "new_exposures": 0, "newest_in_sequence_mjd": 61305.6, "newest_in_sequence_date": "2026-09-22 15:05",
 "newest_available_mjd": 61305.6, "newest_available_date": "2026-09-22 15:05",
 "new_by_band": {}, "new_by_collection": {}, "rebuild_recommended": false}
```

Cached for ten minutes; it refreshes the footprint-search cache, so `POST /api/sequences` with `force: true` then includes the new files.

### Build filters

`POST /api/sequences` also accepts `date_min`, `date_max` (UTC dates `YYYY-MM-DD`, `date_max` inclusive) and `bands` (e.g. `["D5", "D6"]`). Unset filters leave the sequence id unchanged (ADR-0029).

## Archival epochs (2MASS, AllWISE, NEOWISE)

Older-survey images resampled onto a built sequence's own grid, in MJy/sr, so they can be blinked with the
SPHEREx co-adds over a 1999–2026 baseline (ADR-0021). Not available for solar-system objects.

### `GET /api/sequences/{id}/archival`

Query: `fov_arcmin` (optional, 1–60): a wider concentric grid at the same pixel scale, for targets that were
far from today's position decades ago (Barnard's Star sits 4.4′ south of its 2026 position in 2MASS).

- `202` while building (the first call starts the build; poll the same URL): `{"id", "status": "running",
  "stage": "search" | "fetch", "done", "total", "error", "elapsed"}`.
- `200` when built:

```json
{"sequence": "<id>", "version": 2, "created": "...",
 "grid": {"ra": 269.4462, "dec": 4.7688, "n": 119, "pixscale_arcsec": 6.15, "fov_arcmin": 12.2, ...},
 "surveys": ["2mass", "allwise", "neowise"],
 "frames": [
   {"index": 0, "survey": "2MASS", "survey_id": "2mass", "band": "H", "label": "2MASS H", "wl_um": 1.662,
    "mjd": 51692.43, "mjd_min": 51692.42, "mjd_max": 51692.44, "date": "2000-05-28 10:17", "epoch_year": 2000.41,
    "native_pixscale_arcsec": 1.0, "n_images": 4, "unit": "MJy/sr", "coverage": 1.0, "sources": ["https://..."],
    "target_radec": [269.4502, 4.6966], "target_px": [55.6, 15.5], "target_in_field": true},
   ...],
 "array": {"file": "frames.f32", "dtype": "float32", "shape": [47, 119, 119], "unit": "MJy/sr (background subtracted)"},
 "processing": {...}, "stats": {"images_found": 683, "requests": 1344, "bytes": 1080000000, "seconds": 134.2, "errors": []},
 "sources": {...}}
```

Frames are sorted by time. NEOWISE frames are per-visit medians (`n_images` single exposures); `coverage` is
the fraction of grid pixels with data. `404` if the sequence is not built or is a co-moving sequence; `500`
with the task if the build failed (a new request after two minutes retries).

### `GET /api/sequences/{id}/archival.bin`

Query: `fov_arcmin` (as above), `index` (optional). Float32 little-endian, NaN where no data: all frames
`[k, n, n]` in the order of `frames`, or one frame `[n, n]` with `index`. `404` until built.

## Solar-system objects (IRSA MOST)

### `GET /api/moving/search`

Query: `name` (e.g. `3I`, `Eros`, `C/2025 N1`) or `naif_id` (JPL NAIF ID; asteroid number N is 20000000 + N);
`type` (`Asteroid` | `Comet`, guessed from the name when omitted); `begin`, `end` (UTC dates, default
2025-04-01 to today).

- `202` while MOST searches (long windows take minutes; poll the same URL).
- `200`:

```json
{"object": "ATLAS (C/2025 N1)", "query": {...}, "elements": {"eccentricity": "6.1396", ...},
 "n_files": 160, "n_exposures": 84, "date_min": "2025-08-01 08:00", "date_max": "2025-08-15 12:42",
 "exposures": [{"mjd": 60888.334, "date": "...", "ra": 252.664, "dec": -17.4905, "band": "D3", "obs_id": "...",
                "collection": "spherex_dr1", "uri": "ibe/data/spherex/dr1/level2/...", "vmag": 14.04,
                "sun_dist": 3.48, "geo_dist": 2.81, "phase": 13.9}, ...],
 "ephemeris": [[60888.3334, 252.664196, -17.490529], ...],
 "suggested_request": {"ra": 247.109, "dec": -16.840, "name": "ATLAS (C/2025 N1)", "fov_arcmin": 6.0,
                       "object_name": "3I", "object_type": "Comet", "naif_id": null,
                       "obs_begin": "2025-08-01", "obs_end": "2025-08-20"}}
```

- `409` for ambiguous names: `{"status": "error", "kind": "AmbiguousObject", "error": "...", "choices":
  [{"name": "16 Psyche", "type": "Asteroid", "naif_id": 20000016, "designation": "A852 FA"}, ...]}`; retry with
  `naif_id`.
- `404` unknown object, `502` MOST unavailable, `422` without `name` or `naif_id` or with a bad date.

### Co-moving sequences

`POST /api/sequences` with the `suggested_request` fields (`object_name`, `object_type`, `naif_id`,
`obs_begin`, `obs_end` plus `ra`, `dec`, `fov_arcmin`) builds a sequence in which every exposure is resampled
on a grid centred on the object at its own time (ADR-0022). Its `meta.json` adds `grid.frame = "comoving"`,
`exposures[i].center` and `epochs[i].center` ([ra, dec] of that grid's centre), `target.ephemeris`, and a
`moving` block; `target_px` and the track sit on the central pixel. Stars trail; catalog overlays and archival
epochs do not apply.
