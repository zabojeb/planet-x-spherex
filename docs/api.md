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

`GET /api/resolve?q=<text>` resolves, in this order: a preset id or name; coordinates (`269.452 4.693`, `17:57:48.5 +04:41:36`, `17h57m48.5s 4d41m36s`); an exact name or alias from the local object index (case, spacing and punctuation ignored: `alpha Lyr`, `α Lyr`, `gliese 699`, `WISE 0855-0714`); earlier Sesame answers cached for 30 days; and finally CDS Sesame, asking all configured mirrors at once. Returns a target as above (`pmra`/`pmdec` = 0 when unknown; `fov_arcmin` is set for extended objects such as M 31). `404` if the name is unknown (remembered for 10 minutes) or every resolver is unreachable ([ADR-0021](adr/0021-local-object-index-for-search.md)).

```bash
curl "http://<api-host>/api/resolve?q=RW%20Aur"
```

`GET /api/objects` returns the local object index the viewer searches while you type: about 4,500 SIMBAD objects (brown dwarfs, fast-moving and variable stars, young stars, named stars, Messier objects, galaxies, nebulae, clusters) with their search aliases. It is a static file, 712 kB (237 kB gzipped), sent with an `ETag` and `Cache-Control: public, max-age=3600`; `If-None-Match` gives `304`.

```json
{
  "version": 1,
  "generated": "2026-11-14",
  "source": "SIMBAD astronomical database (CDS, Strasbourg) via TAP; built by scripts/build_object_index.py",
  "fields": ["name", "aliases", "otype", "cat", "ra", "dec", "pmra", "pmdec", "plx", "vmag", "kmag", "sp", "size", "fov", "refs", "preset"],
  "categories": {"brown-dwarf": "Brown dwarf", "young": "Young star", "...": "..."},
  "types": {"BD*": "Brown Dwarf", "Mi*": "Mira Variable", "...": "..."},
  "rows": [["Vega", ["α Lyr", "alf Lyr", "alpha Lyrae", "alpha Lyr", "GJ 721", "GJ 721.0", "HD 172167", "HIP 91262"], "dS*", "nearby", 279.23473, 38.78369, 200.9, 286.2, 130.23, 0.03, 0.13, "A0V", null, null, 2817, null]]
}
```

Rows are sorted by `refs` (SIMBAD bibliography count). `ra`/`dec` are degrees ICRS at J2000, `pmra`/`pmdec` mas/yr, `plx` mas, `size` arcmin, `fov` a suggested field in arcmin for extended objects, `preset` the id of the challenge preset the row stands for. Rebuild with `scripts/build_object_index.py` ([development.md](development.md#object-index)).

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
{"id": "48006f61db3e73e6ae51", "status": "running", "stage": "fetch", "done": 120, "total": 358, "error": null, "position": null, "preview": "48006f61db3e73e6ae51p1", "elapsed": 9.4}
```

`status` is `queued`, `running`, `done`, `error` or `cancelled`; `stage` is `queued`, `search`, `fetch` (with `done`/`total` exposures), `stack` or `done`. While queued, `position` is the number of builds ahead of this one. The `id` is a hash of the normalised parameters, so identical requests share one job and one result.

`preview` is the id of the latest preview while the build runs, else `null` (ADR-0039). A preview is a complete result of the epochs read so far: the epoch the viewer opens on first, then more at 1/3 and 2/3 of the exposures. Builds of fewer than 40 exposures, or with one epoch, have none. Read it like any finished result (`GET /api/sequences/{preview}` or `/lite`, its planes and tables). Its `meta.json` has `preview: {"of": <build id>, "n": <1, 2, …>, "frames": <exposures in it>, "total": <exposures in the build>}`. Previews are deleted once the full result is written, so a client should drop a preview's data when the build finishes. Work that is stored with a sequence or handed out answers `404` with a message for a preview: field and point analyses, known objects, Track & Stack, verification, position photometry, archival epochs and exports. Quick reads (planes, tables, motion, periodogram, spectra) work on it.

Builds go through the job queue (ADR-0033): `SPHEREX_JOBS_PER_WORKER` builds run at once per worker (default 2; `SPHEREX_MAX_JOBS` is accepted as an alias), the rest wait by priority and submission order, and a queued job reports `position` (builds that start before it). Each POST and each poll is a heartbeat keyed by `X-Client-Id`; `DELETE` withdraws the caller. A build is cancelled when every client has withdrawn, or when nobody polled it for `SPHEREX_INTEREST_TIMEOUT` seconds (default 30; alias `SPHEREX_JOB_IDLE`); a cancelled job answers 410. A build that runs longer than `SPHEREX_MAX_RUNTIME` seconds (default 900; alias `SPHEREX_JOB_TIMEOUT`) fails with an error. Limits answer 429 with `Retry-After`.

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
| `500` | the job, with `error` set (failed or timed out) |
| `410` | the job, cancelled because nobody was waiting; POST again to restart it |
| `404` | unknown id |

### Stop waiting

`DELETE /api/sequences/{id}` → `204`. The caller no longer waits for the build; once no client waits, it is cancelled and its slot goes to the next build in the queue. A finished result is kept. `404` for an unknown id. The web client sends it when the target or field of view changes and when the page is closed.

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
| `stats` | `frames_found`, `frames_used`, `errors`, `requests`, `bytes`, `cache_hits`, `retries`, `seconds` (`search`, `fetch_and_align`, `assemble`, `total`), `timings`: per-exposure seconds as `{n, p50, p90, max, sum}` for `slot` (waiting to start), `read` (cutout from S3), `tables` (wavelength map and ePSF), `pool_wait` (waiting for a CPU worker), `process` (align and measure) and `work` (all of it) |
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

`GET /api/catalogs` lists the available catalogues: `gaia` (Gaia DR3, ESA), `catwise` (CatWISE2020, IRSA), `2mass` (2MASS point sources, IRSA), `simbad` (SIMBAD named objects, CDS).

`GET /api/catalogs/{name}?ra=<deg>&dec=<deg>&radius_arcmin=<r>` (radius default 5, maximum 30) returns up to 3,000 sources:

```json
[{"id": "Gaia DR3 4472832130942575872", "ra": 269.4485, "dec": 4.7394, "epoch": 2016.0,
  "pmra": -801.55, "pmdec": 10362.39, "parallax": 546.98, "mag": 8.19, "mag_band": "G", "color": 2.83}]
```

Every source has `epoch` (Julian year of `ra`/`dec`) and `pmra`/`pmdec` in mas/yr (null when unknown), so clients can place it at any date.

`simbad` rows come most-studied first and add `type` (object type, e.g. `BY Dra Variable`), `otype` (its short code), `sp_type`, `z` (redshift), `refs` (number of references) and `parallax`; `mag` is V, else Gaia G, else J, else K, named in `mag_band`; the `NAME ` prefix of SIMBAD identifiers is dropped (`Barnard's star`). SIMBAD services are tried in order from `SPHEREX_SIMBAD_TAP_URLS` (default: CDS, then the CfA mirror), so the layer keeps working while one of them is down. `502` with a readable `detail` (naming the catalogue) if the upstream service fails, times out or returns something unusable; the client can simply retry. The example values above are illustrative, not a recorded response.

## Analyses of a built sequence

All return `404 {"detail": "sequence not ready"}` for an unknown or unfinished id and `422` for bad parameters.

### `POST /api/sequences/{id}/photometry`

Forced photometry of any position (optionally moving) in every exposure, on the sequence's cached cutouts (ADR-0031). Synchronous, a few seconds. Body: `{"ra": deg, "dec": deg, "pmra": 0, "pmdec": 0, "ref_epoch": 2000.0, "name": null}` (`pmra` includes cos dec, mas/yr; `ref_epoch` is the Julian year of `ra`/`dec`, e.g. 2016.0 for Gaia DR3).

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

Proper motion from per-epoch co-add centroids (ADR-0030). Query: `x`,`y` (grid pixels, 0-based) or `ra`,`dec`; optional `bands=D1,D2`, `pmra`,`pmdec` (prior motion used to predict where to look).

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

Lomb–Scargle of the target's light curve, with fluxes normalised per wavelength bin exactly as the web client does (ADR-0031). Query (GET) or body fields (POST, plus `photometry`: records like meta.json's): `method=standard|multiband`, `band`, `min_period` (days, default 0.5), `max_period` (default the baseline), `include_flagged=false`.

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

All are sent with `Content-Disposition: attachment`. FITS headers carry the grid WCS, `BUNIT = MJy/sr`, dates, wavelength range, frame count and provenance (ADR-0030).

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

`POST /api/sequences` also accepts `date_min`, `date_max` (UTC dates `YYYY-MM-DD`, `date_max` inclusive) and `bands` (e.g. `["D5", "D6"]`). Unset filters leave the sequence id unchanged (ADR-0031).

## Archival epochs (2MASS, AllWISE, NEOWISE)

Older-survey images resampled onto a built sequence's own grid, in MJy/sr, so they can be blinked with the
SPHEREx co-adds over a 1999–2026 baseline (ADR-0023). Not available for solar-system objects.

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
on a grid centred on the object at its own time (ADR-0024). Its `meta.json` adds `grid.frame = "comoving"`,
`exposures[i].center` and `epochs[i].center` ([ra, dec] of that grid's centre), `target.ephemeris`, and a
`moving` block; `target_px` and the track sit on the central pixel. Stars trail; catalog overlays and archival
epochs do not apply.

## Spectral analysis

All spectral endpoints work on photometry records shaped like `meta.photometry` (`wl`, `band`, `flux_mjy`,
`err_mjy`, `epoch`, `flagged`, `relative`). Sequence endpoints are memoised per process and sent with
`Cache-Control: public, max-age=86400`; every payload carries `version` (the analysis version). Method:
[methods](methods.md#spectral-analysis), ADRs 0040–0044.

### `GET /api/sequences/{id}/spectrum?include_flagged=false`

```json
{
  "sequence": "d651cb1431ed20027191", "target": "ε Indi B", "version": 1,
  "grid": {"bins_per_detector": 17, "n_bins": 102},
  "n_measurements": 458,
  "bins": [{"band": "D1", "bin": 3, "wl": 0.861, "wl_lo": 0.852, "wl_hi": 0.873, "flux_mjy": 1.92,
            "err_mjy": 0.03, "n": 5, "n_rejected": 0, "chi2": 0.8, "relative": false, "epochs": [0, 1],
            "continuum_mjy": 2.10}],
  "relative_bins": [ ...same shape, ring fluxes of saturated stars (relative: true)... ],
  "epochs": [{"epoch": 0, "bins": [...], "indices": {...}}],
  "features": [{"id": "ch4_3.3", "name": "CH₄ 3.3 µm", "species": "CH4", "kind": "absorption", "phase": "gas",
                "center": 3.3, "band": [3.15, 3.45], "blue": null, "red": [3.85, 4.15], "depth": 0.80,
                "depth_err": 0.003, "ew_um": 0.24, "significance": 243.4, "detected": true, "n_band": 5}],
  "detections": [{"kind": "absorption", "wl_min": 1.33, "wl_max": 1.47, "wl_center": 1.40, "depth_max": 0.85,
                  "ew_um": 0.11, "significance": 48.5, "n_bins": 4, "identified_as": ["h2o_1.4"]}],
  "continuum": {"available": true, "fraction_bins_below_3sigma": 0.31, "reliable": false, "note": "..."},
  "indices": {"ch4_3.3": {"value": 0.19, "err": 0.004, "numerator_um": [3.2, 3.4],
                          "denominator_um": [3.9, 4.1], "description": "..."}},
  "notes": ["..."]
}
```

`bins` is the absolute spectrum (calibrated, unflagged points); it is empty for saturated stars, whose
measurements are all relative (`relative_bins`, see `notes`). `features[].detected` means ≥ 3σ in the
feature's sense (absorption below, emission above the continuum). Use `detections` only when
`continuum.reliable` is true. Indices: `h2o_1.4`, `ch4_1.6`, `h2o_1.9`, `ch4_2.2`, `ch4_3.3`, `co_2.3`,
`peak_4um_J`, `slope_K_4.5` (value `null` when the denominator is not detected).

### `GET /api/sequences/{id}/classify?catalogs=false`

```json
{
  "sequence": "...", "target": "...", "version": 1, "available": true,
  "summary": {"label": "T3.0V (methane at 3.3 µm)", "family": "brown_dwarf",
              "notes": ["Methane: CH4(3.3)/4 µm = 0.19 ...", "..."], "context_suppressed": ["h2o_ice_3.05"]},
  "templates": {"best": {"name": "spex_T3.0", "sptype": "T3.0V", "family": "dwarf_standard",
                         "source": "SpeX Prism standard (SPLAT)", "chi2_red": 8.6, "probability": 1.0, "n_bins": 50},
                "ranked": [ ...top 8, same shape... ],
                "class_probabilities": {"T": 1.0, "L": 0.0},
                "max_wl_um": 2.45, "n_bins": 50},
  "template_error": null,
  "blackbody": {"teff_k": 1752, "teff_range_k": [1700, 1800], "a_k_mag": 0.0, "chi2_red": 235.7, "n_bins": 102,
                "max_wl_um": null, "scale": 1.2e-9},
  "blackbody_short": { ...same, fitted to ≤ 2.45 µm... },
  "ir_index": {"alpha": -0.14, "alpha_err": 0.02, "range_um": [2.0, 5.0], "lada_class": "flat spectrum",
               "note": "Indicative: ..."},
  "ir_excess": {"3_3.8um": {"observed_mjy": 14.5, "predicted_mjy": 11.3, "ratio": 1.29, "significance": 3.2},
                "3.8_5um": {"...": "..."}, "reference_fit": "blackbody to 2.45 um (...)"},
  "model_curves": {"best_template": {"source": "spex_T3.0", "wl": [...], "flux_mjy": [... null beyond 2.45 µm]},
                   "blackbody": {"wl": [...], "flux_mjy": [...]}},
  "synthetic_photometry": {"J": {"coverage": 1.0, "extrapolated": false, "flux_mjy": 36.1, "mag": 12.1,
                                 "mag_err": 0.01}, "H": {...}, "Ks": {...}, "W1": {...}, "W2": {...}},
  "catalog_comparison": {"bands": {"J": {"synthetic": 12.1, "catalog": 11.9, "delta_mag": 0.21,
                                         "flux_ratio": 0.82, "catalog_id": "2MASS J..."}},
                         "matches": {"2mass": {...source + separation_arcsec...}, "catwise": {...}},
                         "errors": null},
  "notes": []
}
```

`family` is one of `star`, `late_m_dwarf`, `brown_dwarf`, `composite` (star + cold methane source),
`yso` (rising continuum with 3 µm ice), `yso_candidate` (star with a 3.8–5 µm excess), `nonstellar` (no stellar
template fits, rising red power law: AGN/QSO or YSO) or `null`. `catalog_comparison.bands[].suspect` is true
when synthetic and catalogue magnitudes differ by > 0.75 mag (wrong match, blend, variability, or catalogue
photometry of a fast mover).
Without calibrated measurements: `{"available": false, "reason": "...", "synthetic_photometry": {},
"catalog_comparison": null, "notes": [...]}`. `catalogs=true` matches 2MASS and CatWISE within 4″ after
proper motion (seconds; catalogue failures land in `catalog_comparison.errors`). W2 extends past 5 µm and is
computed with a power-law extension (`extrapolated: true`, `coverage` < 1).

### `GET /api/sequences/{id}/spectral-variability?reference=`

```json
{
  "sequence": "...", "version": 1, "available": true, "reference_epoch": 1,
  "kinds": ["relative"], "systematic": 0.03,
  "epochs": [{"epoch": 0, "variable": true, "significant_bands": ["D2", "D3", "D5"],
              "chi2_red_constant": 14.2,
              "per_band": {"D5": {"ratio": 1.23, "err": 0.02, "z": 9.9, "n_bins": 17, "kind": "relative"}},
              "ratios": [{"band": "D5", "bin": 70, "wl": 3.9, "kind": "relative", "ratio": 1.2, "err": 0.04,
                          "z": 5.1}]}]
}
```

Unavailable: `{"available": false, "reason": "fewer than two epochs with measurements", "epochs": [...]}`.

### `POST /api/spectrum/analyze`

Body: `{"photometry": [records...], "target": {"ra", "dec", "pmra", "pmdec", "ref_epoch", "name"} | null,
"catalogs": false, "reference": null}` (records e.g. from `POST /api/sequences/{id}/photometry`). Returns
`{"version", "spectrum": <as /spectrum without sequence/target>, "classification": <as /classify>,
"variability": <as /spectral-variability>, "epochs": [..]}`. 422 for an empty list.

### `GET /api/spectral/reference`

The bin grid (`grid[]`: bin, band, wl_lo, wl_hi, wl_center), the curated `features`, the `indices`
definitions and the template library listing (`templates[]`: name, sptype, family, source).

## Automated field analysis, moving objects, Track & Stack

Jobs below share one lifecycle. `POST` starts or joins the job and returns the job JSON
`{id, status, stage, done, total, error, elapsed}` (status `queued|running|done`); poll `GET /api/field/{id}`:
200 with the result (immutable, `ETag "<id>"`), 202 with the job while it runs (every poll is a heartbeat),
500 if it failed, 410 if cancelled (POST again). `DELETE /api/field/{id}` withdraws this client (200 job / 204);
a job nobody polls for 60 s is cancelled. Send `X-Client-Id`. Job ids are `<sequence>-<kind><hash>` with kind
`f` (field analysis), `k` (known objects), `s` (stack search). 404 for an unknown or unfinished sequence.

### `POST /api/sequences/{id}/field`

"Analyze this field". Body (all optional): `snr_min` (5), `max_sources` (300), `catalogs`, `motion`,
`shift_stack`, `transients`, `known_objects`, `variability`, `colours` (all true), `vmag_lim` (21), `force`.
Result: `{id, sequence, version, created, options, target, grid, epochs[{index, year, date_min, date_max}],
reference_year, sources[], candidates{movers, faint_movers, tracklets, pairs, variables, red, anomalies},
stats{sources, catalogs, transients, anomaly, seconds}, realbogus{model, validation, importance}, notes[]}`.

- `sources[]`: `id, x, y` (grid px), `ra, dec, snr, bands_detected, epochs_detected, flux_mjy{D1..}`,
  `flux_err_mjy, snr_band, saturated, blended_with, matches{gaia|catwise|2mass: {id, mag, mag_band, pmra,
  pmdec, sep_arcsec} | null}`, and when fitted `motion{pmra, pmdec, pmra_err, pmdec_err, pm_total,
  significance, n_epochs, bands, chi2_red, supporting_bands}`, `variability{n, chi2_red, stetson_j,
  amplitude, method}`.
- `movers[]`: `id, source, x, y, ra, dec, category` (`known_mover|discrepant_motion|unknown_mover`),
  `score, motion, catalog_motion{catalog, id, pmra, pmdec} | null, catalog_discrepancy_sigma, saturated,
  track[{epoch, year, ra, dec, x, y}]`.
- `faint_movers[]` (shift-and-stack): `id, x, y, ra, dec, snr, gain_over_static, pmra, pmdec, pm_total,
  at_speed_limit, mover, nearest_source, score`.
- `tracklets[]` (≥ 3 detections in one visit) and `pairs[]`: `id, epoch, n, mjd_mid, rate_arcsec_per_hr,
  pa_deg, rms_arcsec, mean_snr, mean_rb, partner_fraction, score, detections[{exposure, mjd, band, x, y, ra,
  dec, snr, rb, partner_seen}], known` (best identification or null), `identification[{name, kind, vmag,
  sep_arcsec, pos_tolerance_arcsec, dpa_deg, dspeed_frac, speed_arcsec_per_hr, pa_deg,
  o_minus_c_arcsec[{dra, ddec}], score}]`.
- `variables[]`: `id, source, x, y, ra, dec, method` (`aperture|ring`), `n, chi2_red, stetson_j, amplitude,
  epoch_medians[{epoch, rel, n}], period{best_period_days, power, false_alarm_probability, peaks} | null, score`.
- `red[]`: `id, source, x, y, ra, dec, ratio_long_short, snr_long, snr_short, short_absent, no_gaia,
  method` (`source|map`), `excess_snr` (map), `score`.
- `anomalies[]`: `id, source, x, y, ra, dec, score` (isolation forest, 0.5 ordinary → 1),
  `explain[{ratio, z, direction}], no_gaia`.

### `POST /api/sequences/{id}/moving-objects`

Moving Object Detection only (tracklets + identification). Body `{vmag_lim: 21, force: false}`; result
shape as above with the other searches off.

### `POST /api/sequences/{id}/known-objects`

Known asteroids and comets seen from SPHEREx. Body `{times: "epochs" | "exposures", epoch: null, vmag_lim: 21}`.
Result: `{sequence, times_mode, epoch, vmag_lim, observer, times[{mjd, objects[{name, kind, mjd, ra, dec, vmag,
ra_rate, dec_rate, err_ra, err_dec, x, y, inside, speed_arcsec_per_hr, pa_deg}]}]}` (rates in ″/h).

### `POST /api/sequences/{id}/trackstack/search`

Synthetic tracking in one visit. Body `{epoch, rate_min: 0.3, rate_max: 10, window_hours: 48, bands: null,
snr_min: 6}`. Result: `{sequence, epoch, window_mjd, t_ref, frames, trials, rate_range_arcsec_per_hr,
velocity_step_arcsec_per_hr, candidates[{id, x, y, ra, dec, mjd_ref, snr, rate_arcsec_per_hr, pa_deg,
sub_snr[3], sub_detections, score}]}` (a candidate needs S/N ≥ 3 in two of three time thirds).

### `GET /api/sequences/{id}/trackstack.bin`

Track & Stack of one visit, synchronous. Query: `epoch`, `rate` (″/h), `pa` (deg east of north), `combine`
(`mean|median|sum`), `bands` (comma list), `subtract_static`, `parts` (1–3), `part`. Body: float32 LE `[n, n]`;
headers `X-Array-Shape`, `X-Array-Dtype`, `X-Track-Tref` (MJD), `X-Track-Frames`, `X-Track-Bands`. 422 for bad
detectors or `part ≥ parts`.

### `POST /api/sequences/{id}/verify`

Body `{ra, dec, mjd_ref?, rate: 0, pa: 0, epoch?, max_exposures: 40}`. Result `{sequence, input, summary{exposures,
fitted, detected_snr3, median_snr, median_offset_arcsec, median_sigma_arcsec, median_chi2_red},
exposures[{index, mjd, band, wl, predicted_ra, predicted_dec, fit{flux_mjy, err_mjy, snr, bkg, chi2_red, npix,
sigma_x, sigma_y, ra, dec, sigma_arcsec, offset_arcsec} | null}], strips{half_size_px, stare[{index, pixels}],
track[{index, pixels}]}}` (pixels: 11×11 lists, null for no data).

### `GET /api/field/{id}/ades.psv`

The tracklets of a finished field / moving-objects job as ADES PSV for station 288 with SPHEREx's geocentric
position per observation (`sys` ICRF_KM, `ctr` 399), `astCat` Gaia3, note Z. Follow-up data, not a submission.
502 if Horizons fails.

Tracklet identification (ADR-0054) queries known-object positions at each tracklet's first and last detection;
each `identification[]` candidate is `{name, kind, vmag, sep_arcsec, pos_tolerance_arcsec, dpa_deg, dspeed_frac,
motion_ok, speed_arcsec_per_hr, pa_deg, o_minus_c_arcsec[{dra, ddec}], score}` with speed and direction from the
displacement between the two queried positions; `stats.known_objects.positions` says so.

## Point analysis ("click a point → Analyze")

Every automatic analysis of one position of a finished sequence in one job (ADR-0053). Same lifecycle as field
jobs: deduplicated per sequence and position rounded to 1e-4° (motion to 1 mas/yr); send `X-Client-Id`.

### `POST /api/sequences/{id}/analyze`

Body `{ra, dec, pmra?: null, pmdec?: null, ref_epoch: 2000.0, name?: null, force: false}` (deg ICRS; motion in
mas/yr with cos dec; without a motion the analysis follows a Gaia DR3 — else SIMBAD — counterpart within 4″
that has a motion, else a static position). Returns the job `{id, status, stage, done, total, error, elapsed}` — `id` is
`<sequence>-p<hash>`, `status` `done` straight away when the result exists. 404 unknown or unfinished sequence,
422 bad body.

### `GET /api/analyses/{job_id}` · `DELETE /api/analyses/{job_id}`

GET: 200 with the result (immutable, `ETag: "<job_id>"`), 202 with the job while it runs, 500 failed, 410
cancelled (POST again), 404 unknown; every GET is a heartbeat. The steps run concurrently (ADR-0056): `stage` lists
the steps running now (e.g. `"photometry, known_objects"`, then `"verdict"`), `done`/`total` count finished steps,
and `steps` gives each step's state: `{"identification": "done", "photometry": "running", ...}` with values
`pending | running | done | error`. DELETE withdraws this client (200 job / 204 unknown); a job nobody polls for
60 s is cancelled. `force: true` re-runs a finished analysis.

The whole analysis returns within 60 s: a step still running then becomes `{"error": "TimeoutError: not finished
within 60 s; ..."}` (repeat later — downloads and catalogue answers are cached). Remote services use short timeouts
and a circuit breaker: a service that just failed is reported at once as `ServiceUnavailable: <host> is
unavailable ...` instead of being waited for again. The result's `timings` block shows
`{total_s, steps_s: {step: seconds}, timed_out: [steps], services_unavailable: {host: {retry_in_s, error}}}`.

Result (every step slot is either its object or `{"error": "<Type>: <message>"}`):

```text
{ id, sequence, version: 1,
  request: {ra, dec, pmra, pmdec, ref_epoch, name},
  point: {ra, dec, x, y, inside, motion_used: {pmra, pmdec, ref_epoch, source}},
  identification: {radius_arcsec: 15, reference_year,
                   gaia|catwise|2mass|simbad: {matches: [{id, ra, dec, epoch, pmra, pmdec, mag, mag_band,
                                                           otype?, sptype?, parallax?, sep_arcsec, pm_total,
                                                           track: [{epoch, ra, dec, x, y}]}],   # ≤ 3, nearest first
                                               n_in_radius, counterpart}            # counterpart: nearest ≤ 4″
                                    | {error}},
  photometry: {sequence, target, photometry: [records as meta.photometry], rings, stats,
               summary: {n, n_flagged, n_saturated, n_relative, methods: {psf|aperture|ring: n},
                         per_band: {D1..D6: {n, n_good, median_flux_mjy}}}},
  spectrum: {spectrum: <GET /spectrum body without sequence/version>,
             classification: <GET /classify body without sequence/version>,
             variability: <GET /spectral-variability body without sequence/version>},
  motion: <GET /motion body: {start, reference_year, prior, combined, per_band, points, track}>,
  periodogram: <POST /api/periodogram body, curve trimmed to ≤ 400 samples>,
  colours: {long_um, short_um, long_flux_mjy, long_err_mjy, short_flux_mjy, short_err_mjy, n_long, n_short,
            ratio, long_snr, short_snr, red_excess},
  known_objects: {radius_arcsec: 30, observer, epochs: [{epoch, mjd, n_in_box, near: [...]}],
                  matches: [{name, kind, vmag, sep_arcsec, ra, dec, epoch}]},
  tracklets: {available, reason?, field_job?,
              candidates: [{category, kind: "tracklet"|"source", id, sep_arcsec, ...}]},   # ≤ 20 within 20″
  verdict: {novelty: 0..1, headline,
            findings: [{kind: identity|motion|spectrum|variability|colour|solar_system|data,
                        text, confidence: 0..1, novelty: -0.3..0.55, evidence: {...}}]} }
```

```bash
curl -s -X POST -H 'Content-Type: application/json' -H 'X-Client-Id: tab1' \
  -d '{"ra": 269.4462, "dec": 4.7688}' https://<host>/api/sequences/<id>/analyze
```

## Data sources and the file-catalogue mirror (ADR-0057)

### `GET /api/status/upstreams`

Every remote source per kind of data, in the order it is asked now, plus the local mirror:

```json
{"kinds": {"gaia": [{"rank": 0, "name": "mast-gaia", "label": "MAST (STScI)", "service": "mast.stsci.edu", "note": "",
                     "samples": 12, "success_rate": 1.0, "p50_s": 0.31, "p90_s": 0.6, "last_ok_s_ago": 4,
                     "last_fail_s_ago": null, "last_error": null, "breaker": null, "state": "ok"},
                    {"rank": 1, "name": "esa-gaia", "...": "...", "state": "down"}],
           "spherex-files": ["..."], "spherex-cutouts": ["..."], "spherex-meta": ["..."], "catwise": ["..."],
           "2mass": ["..."], "simbad": ["..."], "solar-system": ["..."]},
 "breakers": {"gea.esac.esa.int": {"retry_in_s": 211, "error": "timed out"}},
 "mirror": {"path": "...", "bytes": 312000000, "files": 1628230, "releases": {"dr1": {"files": 1179406, "complete": true}},
            "synced_at": "1794739000.1", "age_hours": 0.4, "last_sync": {"...": "..."}},
 "cluster": {"host:pid": {"aws-s3": {"samples": 50, "success_rate": 1.0, "p50_s": 0.08}}}}
```

`state` is `ok` (last call succeeded), `degraded` (last call failed), `down` (circuit breaker open, skipped by
interactive calls) or `unknown` (not used since the process started). `cluster` appears only with Redis: what every
API/worker process reported in its last 15-s summary. Kinds: `spherex-files` (range reads: AWS S3, IRSA ibe,
optional `SPHEREX_FILE_MIRROR_BASE`), `spherex-cutouts` (IRSA cutout service vs range reads), `spherex-meta`
(local mirror, IRSA TAP, IRSA SIA), `gaia` (ESA, MAST, IRSA, VizieR), `catwise`/`2mass` (IRSA, VizieR), `simbad`
(CDS, Harvard), `solar-system` (JPL, IRSA MOST, SkyBoT — geocentric).

`GET /api/releases` answers from the mirror when it is complete (with `"source": "local mirror (synced … h ago)"`)
and falls back to it when IRSA TAP fails; `GET /api/catalogs` labels no longer name one archive, since the answer
may come from any source of that catalogue.
