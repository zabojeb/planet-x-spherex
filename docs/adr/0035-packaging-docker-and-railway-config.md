# ADR-0035: One multi-stage image, docker compose for local runs, Railway config as code

- **Status:** Accepted
- **Date:** 2026-11-14

## Decision

- **`Dockerfile`** (repo root): a Node 22 stage builds `web/dist`; a Python 3.11 slim stage installs the backend (non-editable) and copies the built client; non-root user; `SPHEREX_WEB_DIST`, `SPHEREX_CACHE_DIR=/cache`, JSON logs; `deploy/start.sh` picks the role and sizes uvicorn workers from the CPU quota; graceful shutdown (uvicorn 20 s; workers requeue running jobs).
- **`docker-compose.yml`**: api, two workers, Redis and MinIO (as the bucket) — the same topology as Railway.
- **`deploy/e2e_stack.sh`**: the same topology without Docker (Redis + an S3 emulator + 2 API replicas + 2 workers) for hosts without a Docker daemon, plus `deploy/e2e_check.py` and `deploy/loadtest.py`.
- **`.railway/railway.ts`**: Redis, the `spherex-results` bucket in `iad`, and the `api` and `worker` services from the GitHub repo with replicas in `us-east4`; `railway.json`/`railway.toml` are deprecated and not used.
- **Health:** `/api/health` (liveness) and `/api/ready` (Redis and bucket reachable; Railway health check); `/api/queue` reports queue depth and live workers.

## Consequences

- The same image runs locally, on the dev box and on Railway.
- `railway config plan` must be reviewed before the first apply; the TypeScript IaC was written from the skill's documented helpers and has not been planned against a real project yet.
