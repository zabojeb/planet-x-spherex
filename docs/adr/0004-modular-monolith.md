# ADR-0004: Modular monolith first, microservice seams prepared

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

One of the team's objectives is a robust microservice architecture. We have about two days, and the hard, risky part is data access and processing, not service plumbing. We also want an MCP server for AI agents, a command-line path for warming the cache before demos, and background jobs, because a first build takes 30–40 s.

## Decision

Build one Python domain library, `spherex_core`, with no web dependencies, and put thin adapters around it:

- **HTTP API** (`spherex_api`, FastAPI): now.
- **CLI** (`scripts/build_sequence.py`): now.
- **MCP server:** next, as another adapter over the same service functions.

Sequence builds run as in-process jobs: asyncio for network I/O and a process pool for CPU work (ADR-0013).

The code is organised so it can be split into services later without rewriting the domain logic:

| Future service | What it would contain | Contract already in place |
|---|---|---|
| `api` | stateless HTTP and static web files | REST paths in [docs/api.md](../api.md) |
| `worker` | sequence builds | job interface (submit, progress, result directory) |
| `mcp` | MCP tools for agents | the same `spherex_core` service functions |
| `web` | the static SPA | `web/dist` |

Each finished result is a self-contained directory (ADR-0005), so `api` and `worker` would only need a shared volume or object storage.

## Consequences

- One deployable unit: simple to run, debug and demo.
- HTTP, MCP and CLI share one implementation, so results agree between them.
- Running jobs live in memory and are lost on restart (finished results persist on disk).
- Throughput is limited to one machine until the split.

## Alternatives considered

- **Microservices from day one** (API, Celery or RQ workers, Redis, Postgres, object storage): days of plumbing and more failure modes before the first useful image.
- **Serverless functions:** cold starts, payload limits and execution time limits do not suit 30–40 s builds that read hundreds of megabytes.
