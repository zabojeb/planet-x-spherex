# ADR-0055: MCP server so an AI agent can run a discovery search

- Status: Accepted
- Date: 2026-11-14

## Context

The team wants Claude (or any MCP client) to search SPHEREx for new stars, brown dwarfs, asteroids and comets by
itself: choose fields, build sequences, look at the images, run the automated searches, verify candidates
against catalogs and known objects, and write a report. Every capability already exists behind
`spherex_core.services` (the facade the HTTP API uses), but agents need it as tools with LLM-friendly
descriptions, compact outputs, images they can see, and protection against flooding the build queue.

## Decision

- A package `spherex_mcp` built with the official `mcp` Python SDK (v2 `MCPServer`, with a fallback to v1
  `FastMCP`). Tools call `spherex_core.services` directly, with no HTTP round trips.
- Transports: stdio (`python -m spherex_mcp`) for a local Claude Code, and streamable HTTP mounted in the
  FastAPI app at `/mcp` (stateless sessions, DNS-rebinding Host check off for a public site) so a remote agent
  can connect to the deployed viewer. `SPHEREX_MCP=0` disables the endpoint; a missing `mcp` package only logs.
- 20 tools: targets and coverage (`list_presets`, `resolve_target`, `search_observations`), builds
  (`build_sequence`, `sequence_status`, `cancel_job`, `describe_sequence`), images (`get_image` with coadd /
  difference / rgb, `get_blink_strip`, `track_and_stack_image`) returned as PNG image content, analysis
  (`analyze_point`, `spectrum_classify`, `analyze_field`, `known_objects`, `track_and_stack_search`,
  `job_result`), context (`catalog_cone`, `archival_epochs`, `moving_object_search`) and `export_report`. A
  `spherex://overview` resource and a `discovery_report` prompt describe the workflow and the evidence needed
  before calling anything new.
- Long work is start/poll. The server heartbeats the jobs an agent starts (every 10 s, up to 30 min), so a
  slow-thinking agent does not lose its build to the "nobody is waiting" cancellation (ADR-0032); `cancel_job`
  withdraws explicitly.
- Caps per server process: 3 active builds, 30 builds per hour, 2 active analyses, field of view ≤ 20′, on top of
  the queue's own per-client limits.
- Outputs are compacted: floats rounded to 4 significant digits, lists truncated with a count, arrays omitted.
  Images are rendered with numpy + zlib only (PNG), north up, with the predicted target marked.
- `analyze_point` uses the point-analysis service when it exists (parallel PR) and otherwise composes position
  photometry, spectral analysis, motion fit, periodogram and a Gaia cone.

## Consequences

- An agent can do a full discovery loop with only this server; `scripts/mcp_discovery_demo.py` is a scripted
  example and a smoke test over HTTP.
- Keep-alive tasks and caps live in one process; with several API replicas each replica has its own caps.
- Tool docstrings are part of the interface: changes to service payloads should be reflected in them.

## Alternatives considered

- An MCP server that calls the public HTTP API: simpler to deploy separately, but doubles serialisation and
  cannot reuse the process pool and caches; it can still be added on top of `scripts/mcp_discovery_demo.py`.
- Pinning agent builds (no cancellation): robust but lets an abandoned agent hold workers forever.
