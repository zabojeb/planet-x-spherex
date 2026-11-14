# ADR-0014: Development compute on a vast.ai instance next to S3

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The data live in S3 us-east-1 (Virginia). From the team's location each small range request took 1.5–10 s, which makes a 2,000-request build impractical. The team asked to rent compute on vast.ai for development and experiments, and we may train small ML models.

## Decision

Rent one on-demand (not interruptible), verified vast.ai instance on the US east coast:

- Pennsylvania; RTX 4080 16 GB; 32 vCPU (EPYC 7742); 63 GB RAM; 150 GB disk; ~0.9 Gbit/s.
- $0.273 per hour while running; about $0.02 per hour for the disk while stopped.
- Image `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` (Python 3.11), with the astro stack in `/workspace/venv`.

Workflow and rules:

- Access by SSH with a key dedicated to this instance.
- The API runs in a `tmux` session on container port 8000; vast.ai maps container ports to random public ports.
- `scripts/deploy.sh` syncs the repository with `rsync`, installs the backend and restarts the API.
- Connection details live in a git-ignored `.env.deploy`; the repository only has `.env.example`.
- No GitHub tokens or other secrets go on the host, because it belongs to a marketplace provider rather than a data centre. Code reaches it over SSH from a developer machine.

## Consequences

- Measured 67 ms median per S3 request from the instance versus 1.5–10 s from the team's location, about 25× faster or more.
- A GPU is available for ML experiments.
- The instance is a Docker container: `docker compose` does not run inside it, so services run directly (uvicorn in tmux).
- Marketplace reliability: the host could go away. Everything on it is reproducible from the repository and the public archive.
- It costs money until it is destroyed; stop it when idle ([development.md](../development.md#cost-and-lifecycle)).
- The public URL is plain HTTP on a random port: fine for development, not for the final public demo.

## Alternatives considered

- **AWS in us-east-1:** the lowest latency to the bucket, but needs an account with credits.
- **A regular VPS on the US east coast:** similar latency and better for a stable public demo; still an option for deployment.
- **Laptops:** too far from the data.
