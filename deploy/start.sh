#!/bin/sh
# Container entrypoint. SPHEREX_ROLE=api|worker. Sizes itself from the cgroup CPU quota.
set -e
CPUS=$(python -c "from spherex_core.runtime import cpu_quota; print(cpu_quota())")
if [ "${SPHEREX_ROLE:-api}" = "worker" ]; then
  echo "worker: ${CPUS} CPUs, ${SPHEREX_JOBS_PER_WORKER:-2} builds at a time"
  exec python -m spherex_core.jobs.worker
fi
# The in-process queue keeps jobs in memory, so it needs exactly one server process.
if [ -z "${REDIS_URL}${SPHEREX_REDIS_URL}" ] && [ "${SPHEREX_QUEUE:-inprocess}" != "redis" ]; then
  WORKERS=1
else
  WORKERS=${WEB_CONCURRENCY:-$(( CPUS * 2 > 8 ? 8 : CPUS * 2 ))}
fi
echo "api: ${WORKERS} uvicorn workers on port ${PORT:-8080}"
exec uvicorn spherex_api.main:app --host 0.0.0.0 --port "${PORT:-8080}" --workers "${WORKERS}" \
  --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20 --no-access-log
