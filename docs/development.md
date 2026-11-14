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
| `SPHEREX_MAX_CONNECTIONS` | 96 | HTTP connections to S3 |
| `SPHEREX_MAX_FRAMES_IN_FLIGHT` | 64 | exposures read concurrently |
| `SPHEREX_CPU_WORKERS` | 24 | processes for per-exposure work |
| `SPHEREX_TAP_CACHE_HOURS` | 12 | lifetime of cached archive queries |

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
