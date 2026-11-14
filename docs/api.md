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
{"id": "48006f61db3e73e6ae51", "status": "running", "stage": "fetch", "done": 120, "total": 358, "error": null, "position": null, "elapsed": 9.4}
```

`status` is `queued`, `running`, `done`, `error` or `cancelled`; `stage` is `queued`, `search`, `fetch` (with `done`/`total` exposures), `stack` or `done`. While queued, `position` is the number of builds ahead of this one. The `id` is a hash of the normalised parameters, so identical requests share one job and one result.

At most `SPHEREX_MAX_JOBS` builds (default 3) run at once; the rest wait in submission order. Each POST makes the caller one of the build's clients. A queued or running build is cancelled when nobody waits for it: `SPHEREX_JOB_DETACH_GRACE` seconds (default 5) after its last client sent `DELETE`, or when no client polled it for `SPHEREX_JOB_IDLE` seconds (default 120). A build that runs longer than `SPHEREX_JOB_TIMEOUT` seconds (default 900) fails with an error. A client that submits and never polls must poll at least every `SPHEREX_JOB_IDLE` seconds, or set it to 0.

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

`GET /api/catalogs` lists the available catalogues: `gaia` (Gaia DR3, ESA), `catwise` (CatWISE2020, IRSA), `2mass` (2MASS point sources, IRSA).

`GET /api/catalogs/{name}?ra=<deg>&dec=<deg>&radius_arcmin=<r>` (radius default 5, maximum 30) returns up to 3,000 sources:

```json
[{"id": "Gaia DR3 4472832130942575872", "ra": 269.4485, "dec": 4.7394, "epoch": 2016.0,
  "pmra": -801.55, "pmdec": 10362.39, "parallax": 546.98, "mag": 8.19, "mag_band": "G", "color": 2.83}]
```

Every source has `epoch` (Julian year of `ra`/`dec`) and `pmra`/`pmdec` in mas/yr (null when unknown), so clients can place it at any date. `502` with a readable `detail` (naming the catalogue) if the upstream service fails, times out or returns something unusable; the client can simply retry. The example values above are illustrative, not a recorded response.
