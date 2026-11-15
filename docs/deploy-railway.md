# Deploying to Railway

Architecture: [ADR-0032](adr/0032-api-worker-split-on-railway.md) (services), [ADR-0033](adr/0033-job-queue-heartbeats-and-cancellation.md) (queue and cancellation), [ADR-0034](adr/0034-immutable-http-caching-and-light-endpoints.md) (caching), [ADR-0035](adr/0035-packaging-docker-and-railway-config.md) (packaging).

```
browser ──► api ×N (FastAPI + SPA, stateless) ──► Redis (queue, job state, heartbeats, rate limits)
               │  local disk cache ◄── download once ──┐
               ▼                                       │
            bucket "spherex-results" ◄── publish ── worker ×M ──► nasa-irsa-spherex (AWS S3 us-east-1)
```

Everything runs in US East: services in `us-east4`, the bucket in `iad`, next to the SPHEREx data.

## Resource plan

| Resource | Setting | Why |
|---|---|---|
| `api` service | 2 replicas, start with 1 vCPU / 1 GB each | Stateless; 2 uvicorn workers per vCPU (max 8). One replica served ~1,400 cached requests/s in our test. |
| `worker` service | 2 replicas, up to 8 vCPU / 4 GB each, `SPHEREX_JOBS_PER_WORKER=2` | Builds are CPU-bound (one process per vCPU, ~200 MB each). Two replicas give 4 concurrent builds. |
| Redis | Railway Redis, default size | Queue, job state, counters; a few MB. |
| Bucket | `spherex-results`, region `iad` | Finished results, about 1–20 MB per sequence. |

### Cost estimate (Railway usage pricing, check the current rates before applying)

Railway bills used CPU and RAM per minute, not the allocation. At roughly $20 per vCPU-month, $10 per GB-month of RAM and $0.05 per GB of egress:

- **Idle or light use** (the API mostly answers from cache, a few builds per hour): api ≈ 2 × (0.1 vCPU + 0.3 GB) ≈ $10, workers idle ≈ 2 × 0.5 GB ≈ $10, Redis ≈ $3, bucket and egress < $3. **About $25/month.**
- **Busy** (builds most of the day, tens of GB served): workers ≈ 2 × (2 vCPU + 3 GB) ≈ $140, api ≈ $40, Redis and egress ≈ $10. **About $190/month.**
- **A hackathon weekend** at light-to-moderate use: **about $5–15**.

## First deployment

Run from the repository root. Commands that need a linked project are marked.

1. Sign in: `railway login` (already done on this machine).
2. Create the project: `railway init --name seek-in-space`.
3. Add Redis: `railway add --database redis --json`.
4. Create the bucket in US East: `railway bucket create spherex-results --region iad --json`.
5. Create the services (linked project): `railway add --service api --json` and `railway add --service worker --json`.
6. Bucket credentials for both services:

```bash
railway bucket credentials --bucket spherex-results --json
```

```bash
for s in api worker; do railway variable set --service $s SPHEREX_BUCKET=<bucketName> SPHEREX_BUCKET_ENDPOINT=<endpoint> AWS_ACCESS_KEY_ID=<accessKeyId> AWS_SECRET_ACCESS_KEY=<secretAccessKey> AWS_DEFAULT_REGION=<region> SPHEREX_BUCKET_URL_STYLE=<urlStyle>; done
```

7. Redis URL and roles:

```bash
railway variable set --service api 'REDIS_URL=${{Redis.REDIS_URL}}' SPHEREX_ROLE=api SPHEREX_LOG_JSON=1
```

```bash
railway variable set --service worker 'REDIS_URL=${{Redis.REDIS_URL}}' SPHEREX_ROLE=worker SPHEREX_JOBS_PER_WORKER=2 SPHEREX_LOG_JSON=1
```

8. Region and replicas (or apply `.railway/railway.ts` after `railway config plan`):

```bash
railway environment edit --service-config api deploy.multiRegionConfig '{"us-east4-eqdc4a":{"numReplicas":2}}'
```

```bash
railway environment edit --service-config worker deploy.multiRegionConfig '{"us-east4-eqdc4a":{"numReplicas":2}}'
```

```bash
railway environment edit --service-config api deploy.healthcheckPath /api/ready
```

9. Deploy both services from the repo root (the root `Dockerfile` is used automatically):

```bash
railway up --service api --detach -m "first deploy"
```

```bash
railway up --service worker --detach -m "first deploy"
```

10. Public URL for the API: `railway domain --service api`.
11. Verify: `railway deployment list --service api --json` shows SUCCESS, then `curl https://<domain>/api/ready` returns `"backend":"redis","workers":2,"bucket":true`.
12. Warm the presets so the first visitors never wait: POST each preset once (the web client's landing page does this on first visit too).

## Scaling

- More concurrent builds: raise `worker` replicas, or `SPHEREX_JOBS_PER_WORKER` on bigger replicas.
- More page traffic: raise `api` replicas. Results are immutable, so a CDN in front of the custom domain absorbs most reads.
- Limits: `SPHEREX_MAX_ACTIVE_PER_IP` (3), `SPHEREX_MAX_QUEUE` (40), `SPHEREX_POST_RATE_PER_MIN` (30), `SPHEREX_MAX_RUNTIME` (900 s), `SPHEREX_MAX_FRAMES` (4000), `SPHEREX_INTEREST_TIMEOUT` (30 s).

## Running the same topology locally

- With Docker: `docker compose up --build`, then open http://localhost:8080; `docker compose up --scale worker=3` for more workers.
- Without Docker (e.g. the vast.ai dev box): `deploy/e2e_stack.sh start` (Redis, an S3 emulator, 2 API replicas on ports 8100/8101, 2 workers), then `python deploy/e2e_check.py http://127.0.0.1:8100 http://127.0.0.1:8101` and `python deploy/loadtest.py http://127.0.0.1:8100 <sequence id>`.

## Operating

- Logs are JSON lines (`SPHEREX_LOG_JSON=1`): `railway logs --service worker --lines 200 --json`.
- Queue depth and live workers: `GET /api/queue`.
- Readiness (used by the health check): `GET /api/ready`.
- A redeploy of `worker` requeues running builds; clients keep polling and the build resumes on another replica from its cutout cache.
