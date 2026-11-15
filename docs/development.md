# Development

How to run, test and deploy the project. Architecture is in [architecture.md](architecture.md), decisions in [adr/](adr/README.md).

## Repository layout

| Path | Content |
|---|---|
| `backend/` | Python package: `spherex_core` (domain library) and `spherex_api` (FastAPI) |
| `backend/tests/` | unit tests and network tests against the real archive |
| `web/` | web viewer (Vite + React + TypeScript), in progress |
| `scripts/deploy.sh` | sync to the dev server, install, restart the API |
| `scripts/build_sequence.py` | build sequences from the command line, warm the cache, write quick-look PNGs |
| `scripts/build_object_index.py` | rebuild the local object index (`backend/spherex_core/targets/objects.json`) from SIMBAD |
| `docs/` | documentation and ADRs |

## Backend

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/) (or pip).

```bash
uv venv .venv
```

```bash
source .venv/bin/activate
```

```bash
uv pip install -e "backend[dev]"
```

Run the API locally:

```bash
SPHEREX_CACHE_DIR=./cache uvicorn spherex_api.main:app --reload --port 8000
```

Then open `http://localhost:8000/docs`. Building sequences from a laptop works but is slow: each S3 request took 1.5–10 s from the team's location, against 67 ms from a server in US-East. Do real work on a machine close to us-east-1 ([ADR-0014](adr/0014-dev-compute-on-vast-ai.md)).

> The backend is being restructured into the layout in [architecture.md](architecture.md#backend-module-map). If the app module moves, the uvicorn target above changes accordingly.

### Configuration

All settings are environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `SPHEREX_CACHE_DIR` | `~/.cache/spherex` | all caches and sequence results (`/workspace/cache` on the dev server) |
| `SPHEREX_COLLECTIONS` | `spherex_dr1,spherex_dr1_deep,spherex_qr3,spherex_qr3_deep` | collections searched, in order of preference |
| `SPHEREX_TAP_URL` | `https://irsa.ipac.caltech.edu/TAP/sync` | IRSA TAP |
| `SPHEREX_S3_BASE` | `https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/` | Level 2 files |
| `SPHEREX_IRSA_BASE` | `https://irsa.ipac.caltech.edu/` | IRSA download base |
| `SPHEREX_GAIA_TAP_URL` | `https://gea.esac.esa.int/tap-server/tap/sync` | Gaia DR3 |
| `SPHEREX_SESAME_URL` | `https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/~SNV` | name resolver |
| `SPHEREX_MAX_CONNECTIONS` | 96 | HTTP connections to S3 for remeasuring and archival epochs (a sequence build opens 3 per exposure in flight) |
| `SPHEREX_MAX_FRAMES_IN_FLIGHT` | 64 | exposures a build reads at once, shared out over the pool workers (at least 2 per batch) |
| `SPHEREX_CPU_WORKERS` | CPU quota − 1 | processes for reading and per-exposure work (ADR-0036) |
| `SPHEREX_FETCH_WORKERS` | 1 | read and process exposures in batches on the pool (ADR-0039); `0` reads them in the calling process, on one core |
| `SPHEREX_CUTOUT_SOURCE` | `irsa` | `irsa`: ask IRSA's cutout service first and read from S3 when it is busy or fails; `s3`: S3 range reads only (ADR-0038) |
| `SPHEREX_IRSA_IN_FLIGHT` | 8 | IRSA cutout requests open at once for a whole build; above ~8 the service answers 503 to most |
| `SPHEREX_IRSA_TIMEOUT` | 15 | seconds before an IRSA cutout request is given up and the exposure read from S3 |
| `SPHEREX_CUTOUT_CACHE_FOV` | 30 | field of view (arcmin) whose columns the per-file cache keeps, so widening the field reads only new rows (ADR-0037) |
| `SPHEREX_TAP_CACHE_HOURS` | 12 | lifetime of cached archive queries |
| `SPHEREX_MIRROR` | 1 | answer image searches from the local file-catalogue mirror when it is complete and fresh (ADR-0057) |
| `SPHEREX_MIRROR_DB` | `<cache>/mirror/spherex.sqlite` | mirror file |
| `SPHEREX_MIRROR_AUTOSYNC` / `SPHEREX_MIRROR_SYNC_INTERVAL` | 1 / 3600 | refresh the mirror from the bucket in a background thread (one process per machine), every N seconds |
| `SPHEREX_MIRROR_BOOTSTRAP` | 0 | let that thread build a missing mirror (~13 min, ~53 GB of header reads); otherwise run `scripts/sync_mirror.py` |
| `SPHEREX_MIRROR_MAX_AGE_HOURS` | 26 | older than this, TAP is asked first and the mirror is the fallback |
| `SPHEREX_FILE_MIRROR_BASE` | — | optional S3-compatible copy of the Level 2 files, asked before AWS S3 (404 there falls through) |
| `SPHEREX_IRSA_FILE_BASE` | `https://irsa.ipac.caltech.edu/ibe/data/spherex/` | IRSA ibe, the fallback for S3 range reads |
| `SPHEREX_HEDGE_READS` | 1 | in interactive builds, start a second source for a range read that runs past ~p90 |
| `SPHEREX_MAST_GAIA_TAP_URL`, `SPHEREX_VIZIER_TAP_URL`, `SPHEREX_VIZIER_ASU_URL`, `SPHEREX_DATALAB_TAP_URL`, `SPHEREX_IRSA_SIA_URL` | see `config.py` | fallback catalogue and search services |
| `SPHEREX_SKYBOT_FALLBACK` | 1 | known objects from SkyBoT (geocentric, flagged) when JPL fails |
| `SPHEREX_POPULAR_PROMOTE_AFTER` | 5 | requests after which a field is prewarmed by `scripts/prewarm_popular.py` |
| `REDIS_URL` (or `SPHEREX_REDIS_URL`) | — | also publish source statistics and request counts to Redis |
| `SPHEREX_JOBS_PER_WORKER` (alias `SPHEREX_MAX_JOBS`) | 2 | builds running at once per worker; the rest queue |
| `SPHEREX_MAX_RUNTIME` (alias `SPHEREX_JOB_TIMEOUT`) | 900 | seconds before a build is stopped with an error |
| `SPHEREX_INTEREST_TIMEOUT` (alias `SPHEREX_JOB_IDLE`) | 30 | seconds without a poll before an unpinned build is cancelled |
| `SPHEREX_JOB_DETACH_GRACE` | 5 | seconds after the last client detached before a build is cancelled |
| `SPHEREX_LOG_LEVEL` | INFO | `DEBUG` adds a line per exposure, retry and progress poll |
| `SPHEREX_LOG_FORMAT` | text | `json` for one JSON object per line |
| `SPHEREX_SLOW_FRAME` | 30 | seconds after which one exposure is logged as WARNING |
| `SPHEREX_SLOW_REQUEST` | 5 | seconds after which an HTTP request is logged as WARNING |

### Logs

Every line reads `time level logger [sequence id] message key=value ...`, with durations in seconds (`s=`). The sequence id follows a build through the job queue, the pipeline and the HTTP requests about it, so one build reads top to bottom with:

```bash
grep 48006f61db3e73e6ae51 /workspace/api.log
```

At INFO a build logs: `job submitted` (or `job joined`, `job cached`), `job started queued_s=` (time spent waiting for a slot), `build start`, `tap query done` or `image search from cache`, `search done`, `fetch progress` every 10 % or 15 s, `fetch done`, one `fetch time <step>` line per step with p50/p90/max/sum over the exposures, `fetch slowest` (the three slowest exposures), `assemble done`, `write done`, `build done` and `job done`. Cancellations, timeouts and failures say so with the stage they reached. Every API request logs `METHOD path status= s=`; progress polls (202) and static files only at DEBUG. Reading the fetch breakdown: a large `slot` means `SPHEREX_MAX_FRAMES_IN_FLIGHT` is the limit, `read`/`tables` is S3 latency (check `retries`), `pool_wait` means the CPU workers are busy (several builds at once or too few `SPHEREX_CPU_WORKERS`), `process` is our own computation. The same numbers are stored in `meta.json` under `stats.seconds` and `stats.timings`.

`scripts/deploy.sh` starts uvicorn with `--no-access-log` (the API logs requests itself) and passes `DEPLOY_LOG_LEVEL` from `.env.deploy` as `SPHEREX_LOG_LEVEL`.

### Tests

```bash
cd backend && python -m pytest -q
```

Network tests (marked `network`) read a real file from S3 and compare against astropy. Skip them offline:

```bash
cd backend && python -m pytest -q -m "not network"
```

### Building sequences from the command line

```bash
python scripts/build_sequence.py barnard
```

```bash
python scripts/build_sequence.py all --fov 6
```

```bash
python scripts/build_sequence.py "133.79 -7.245" --pmra -8118 --pmdec 677 --ref-epoch 2010.4
```

The target is a preset id, `all`, an object name or coordinates. Options: `--fov`, `--pmra`, `--pmdec`, `--ref-epoch`, `--max-epoch-days`. Each run prints exposures, epochs, requests, megabytes and time, and writes `quicklook.png` (epochs × detectors) into the sequence directory. Results land in the same cache the API reads, so running `all` before a demo makes the presets instant, provided the API requests the same parameters.

### Object index

Search suggestions come from `backend/spherex_core/targets/objects.json`, a committed snapshot of about 4,500 SIMBAD objects ([ADR-0021](adr/0021-local-object-index-for-search.md)). Rebuild it when the selection changes or SIMBAD has new objects worth adding:

```bash
python scripts/build_object_index.py --tap https://simbad.harvard.edu/simbad/sim-tap/sync --cache /tmp/simbad_raw.json
```

The script needs only the standard library and the backend package (to link rows to the challenge presets). It runs 19 selections plus a cone per preset and identifier queries in chunks (the selections are listed at the top of the script), picks a display name and up to 8 search aliases per object, and prints a summary per category. `--tap` may be repeated; a service that fails is dropped for the rest of the run (CDS SIMBAD TAP was down on 2026-11-14, the Harvard mirror worked). `--cache` keeps the raw answers so that changes to naming or ranking can be re-run offline. Review the diff of `objects.json` before committing: display names come from heuristics.

## Web

The viewer in `web/` is being built. The expected workflow:

```bash
cd web && npm install
```

```bash
cd web && npm run dev
```

The dev server proxies `/api` to the API given by `API_URL`. `npm run build` writes `web/dist`, which the API serves at `/` after the next deploy.

## Dev server

We develop on a vast.ai instance in US-East next to the data ([ADR-0014](adr/0014-dev-compute-on-vast-ai.md)). Conventions on the server:

| Path | Content |
|---|---|
| `/workspace/app` | the synced repository |
| `/workspace/venv` | Python environment (uv) |
| `/workspace/cache` | caches and sequence results |
| `/workspace/api.log` | API log |
| tmux session `api` | uvicorn on container port 8000 |

### Deploy

1. Copy `.env.example` to `.env.deploy` (git-ignored) and fill in `DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_KEY`, `DEPLOY_DIR` and `API_URL`. vast.ai maps container ports (22, 8000, 5173) to random public ports; find them in the instance details.
2. Run:

```bash
scripts/deploy.sh
```

It syncs the repository with `rsync` (excluding `.git`, `node_modules`, virtual environments and `.env*`), installs the backend into `/workspace/venv`, and restarts the API in tmux. Use `scripts/deploy.sh --no-restart` to sync and install only, for example to run tests or scripts without interrupting the API.

Run tests on the server:

```bash
source .env.deploy && ssh -i "$DEPLOY_KEY" -p "$DEPLOY_PORT" "$DEPLOY_HOST" 'cd /workspace/app/backend && . /workspace/venv/bin/activate && python -m pytest -q'
```

Rules: never put GitHub tokens or other secrets on the instance; it is a marketplace host, not a data centre. Keep host details out of the repository.

### Team access

Each teammate sends their **public** SSH key; the owner of the vast.ai account attaches it:

```bash
vastai attach ssh <instance-id> "ssh-ed25519 AAAA... name"
```

### Cost and lifecycle

The instance bills about $0.273 per hour while running and about $0.02 per hour for its 150 GB disk while stopped.

Stop it when nobody is using it (disk and cache are kept):

```bash
vastai stop instance <instance-id>
```

Start it again (the public IP and ports may change; update `.env.deploy`):

```bash
vastai start instance <instance-id>
```

Destroy it after the hackathon. This deletes the disk and every cached result and cannot be undone:

```bash
vastai destroy instance <instance-id>
```
