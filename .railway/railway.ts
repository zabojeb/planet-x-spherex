// Railway config as code for Seek In Space (plan with `railway config plan`, apply with `railway config apply`).
// One Docker image (repo-root Dockerfile), two roles; everything in US East next to the SPHEREx S3 bucket.
// Bucket credentials are set once with `railway bucket credentials` (see docs/deploy-railway.md) and preserved here.
import { bucket, defineRailway, github, preserve, project, redis, service } from "railway/iac";

export default defineRailway(() => {
  const queue = redis("redis");
  const results = bucket("spherex-results", { region: "iad" });

  const shared = {
    REDIS_URL: queue.env.REDIS_URL,
    SPHEREX_BUCKET: preserve(),
    SPHEREX_BUCKET_ENDPOINT: preserve(),
    AWS_ACCESS_KEY_ID: preserve(),
    AWS_SECRET_ACCESS_KEY: preserve(),
    AWS_DEFAULT_REGION: preserve(),
    SPHEREX_LOG_JSON: "1",
  };

  const api = service("api", {
    source: github("zabojeb/planet-x-spherex", { branch: "main" }),
    env: { ...shared, SPHEREX_ROLE: "api", SPHEREX_MAX_ACTIVE_PER_IP: "3", SPHEREX_MAX_QUEUE: "40" },
    healthcheckPath: "/api/ready",
    replicas: { "us-east4": 2 },
  });

  const worker = service("worker", {
    source: github("zabojeb/planet-x-spherex", { branch: "main" }),
    env: { ...shared, SPHEREX_ROLE: "worker", SPHEREX_JOBS_PER_WORKER: "2", SPHEREX_MAX_RUNTIME: "900" },
    replicas: { "us-east4": 2 },
  });

  return project("seek-in-space", { resources: [queue, results, api, worker] });
});
