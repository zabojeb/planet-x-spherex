#!/usr/bin/env bash
# Run the production topology without Docker (for hosts without a Docker daemon, e.g. the vast.ai box):
# Redis + an S3 emulator (moto) + 2 API replicas (each 2 uvicorn workers, separate disk caches) + 2 workers.
#   deploy/e2e_stack.sh start|stop     (ports: api 8100 and 8101, redis 6380, s3 5050)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN=${E2E_DIR:-/tmp/spherex-e2e}
SHARED_CACHE=${E2E_SHARED_CACHE:-}   # optional: reuse cutouts/wave/epsf/tap from an existing cache
export REDIS_URL=redis://127.0.0.1:6380/0 SPHEREX_BUCKET=spherex-results SPHEREX_BUCKET_ENDPOINT=http://127.0.0.1:5050 \
  SPHEREX_BUCKET_URL_STYLE=path AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=x AWS_DEFAULT_REGION=us-east-1 \
  PYTHONPATH="$ROOT/backend" SPHEREX_INTEREST_TIMEOUT=${SPHEREX_INTEREST_TIMEOUT:-30} SPHEREX_LOG_JSON=1

stop() { pkill -f "spherex-e2e" 2>/dev/null || true; pkill -f "moto.server -H 127.0.0.1 -p 5050" 2>/dev/null || true; pkill -f "spherex_api.main:app --host 127.0.0.1 --port 810" 2>/dev/null || true; redis-cli -p 6380 shutdown nosave 2>/dev/null || true; }

if [ "${1:-start}" = "stop" ]; then stop; exit 0; fi
stop; sleep 1; mkdir -p "$RUN"
redis-server --port 6380 --save "" --appendonly no --daemonize yes --logfile "$RUN/redis.log"
nohup python -m moto.server -H 127.0.0.1 -p 5050 >"$RUN/s3.log" 2>&1 &
sleep 3
python - <<'PY'
import boto3; boto3.client("s3", endpoint_url="http://127.0.0.1:5050", region_name="us-east-1").create_bucket(Bucket="spherex-results")
PY
mkcache() { mkdir -p "$1"; if [ -n "$SHARED_CACHE" ]; then for d in cutouts wave epsf tap catalogs; do ln -sfn "$SHARED_CACHE/$d" "$1/$d"; done; fi; }
for i in 0 1; do
  mkcache "$RUN/api$i"
  SPHEREX_CACHE_DIR="$RUN/api$i" nohup uvicorn spherex_api.main:app --host 127.0.0.1 --port $((8100 + i)) --workers 2 \
    --proxy-headers --forwarded-allow-ips='*' --no-access-log >"$RUN/api$i.log" 2>&1 &
  mkcache "$RUN/worker$i"
  SPHEREX_CACHE_DIR="$RUN/worker$i" SPHEREX_CPU_WORKERS=12 nohup python -m spherex_core.jobs.worker spherex-e2e \
    >"$RUN/worker$i.log" 2>&1 &
done
sleep 6; curl -s http://127.0.0.1:8100/api/ready; echo; curl -s http://127.0.0.1:8101/api/queue; echo
