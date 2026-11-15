# MCP server: let an AI agent search SPHEREx

The viewer ships a [Model Context Protocol](https://modelcontextprotocol.io) server so Claude (or any MCP client)
can run a discovery search by itself: pick fields, build sequences, look at the blink frames, run the automated
field analysis, verify candidates against Gaia / CatWISE / known asteroids, and write a report.

The server is the package `backend/spherex_mcp`; it calls `spherex_core.services` directly (ADR-0055).

## Connect

**Remote (the deployed site)** — the API serves streamable HTTP at `/mcp`:

```bash
claude mcp add spherex --transport http https://<your-host>/mcp
```

**Local (stdio)**, from a checkout with the backend installed (`uv pip install -e backend`):

```bash
claude mcp add spherex -- python -m spherex_mcp
```

Set `SPHEREX_CACHE_DIR` for the stdio server if your cache is not in `~/.cache/spherex`. Reading SPHEREx data from
S3 is fast only near AWS us-east-1, so for real searches use the deployed endpoint or a server in US East.

Other transports: `python -m spherex_mcp --http --port 8765` serves `http://127.0.0.1:8765/mcp` on its own.
Disable the endpoint inside the API with `SPHEREX_MCP=0`.

## Tools

| Tool | What it does |
|---|---|
| `list_presets` | The five challenge targets with coordinates, proper motion, suggested field of view |
| `resolve_target(query)` | Name or coordinates → RA/Dec and proper motion |
| `search_observations(ra, dec)` | Exposures covering a position, per detector and release, grouped into visits |
| `build_sequence(ra, dec, pmra, pmdec, ref_epoch, fov_arcmin)` | Start a build (aligned exposures, per-visit co-adds); returns `sequence_id` |
| `sequence_status(sequence_id)` | Build progress |
| `cancel_job(job_id)` | Withdraw a build or analysis you no longer need |
| `describe_sequence(sequence_id)` | Epochs, frames per detector, target photometry summary |
| `get_image(sequence_id, epoch, band, mode, reference)` | PNG of one epoch: `coadd`, `difference` (movers = red/blue pairs) or `rgb` (cold brown dwarfs are red) |
| `get_blink_strip(sequence_id, band)` | All epochs side by side on one scale |
| `analyze_point(sequence_id, ra, dec)` | Everything about one position: photometry, spectrum class and features, variability, proper motion, period, nearby Gaia |
| `spectrum_classify(sequence_id)` | Template classification of the target with catalog magnitude checks |
| `analyze_field(sequence_id)` → `job_result(job_id)` | All sources: movers (and whether Gaia agrees), faint movers, asteroid/comet tracklets with real/bogus scores, variables, red-excess and anomaly candidates |
| `known_objects(sequence_id)` → `job_result` | Known asteroids and comets in the field, positions from SPHEREx's own location |
| `track_and_stack_search(sequence_id, epoch, rate_min, rate_max)` → `job_result` | Synthetic tracking for faint moving objects inside one visit |
| `track_and_stack_image(sequence_id, epoch, rate, pa)` | PNG of a stack along one motion, to confirm a tracklet |
| `moving_object_search(name)` | SPHEREx exposures of a known asteroid or comet (IRSA MOST) |
| `archival_epochs(sequence_id)` | 2MASS, AllWISE and NEOWISE frames on the same grid: a 26-year baseline |
| `catalog_cone(catalog, ra, dec, radius_arcmin)` | Gaia DR3, CatWISE2020, 2MASS or SIMBAD sources, with high-proper-motion ones listed |
| `comet_search(sequence_id \| ra, dec)` → `comet_candidates(job_id)` | Movers ranked by a 0-100 comet-likeness: coma extent and tail vs field stars, CO2 / CO / H2O bands, a digest2-style orbit class, known/unidentified against JPL (comets included); fast, single-pass and slow (parallax-aware, multi-day) movers. On a co-moving sequence: the same tests on its object per visit |
| `comet_watch()` | Comets bright now (JPL SBDB) and the watched ones with their latest SPHEREx Afrho, CO2 S/N, activity slope and outburst flags |
| `export_report(sequence_id, fmt, job_id)` | CSV / VOTable photometry, ADES astrometry of tracklets (follow-up data), FITS link |

Also a resource `spherex://overview` and a prompt `discovery_report(fields, goal)` that walks an agent through
the search and says what evidence it needs before calling an object new.

### Behaviour worth knowing

- Long work is start/poll. While a job runs the server heartbeats it for the agent (every 10 s, up to 30 min),
  so the queue does not cancel it as abandoned; call `cancel_job` to stop it.
- Caps per server process: 3 active builds, 30 builds per hour, 2 active analyses, field of view up to 20′.
- Outputs are compact (rounded numbers, truncated lists); images are PNG, north up, east left, with the
  predicted target position marked in amber.
- Pixels are 6.2″, and with 3–4 visits proper motions below about 1.2″/yr are not significant. Bright stars
  saturate and only have relative photometry.

## Example session

```text
> Use the spherex tools to look for a brown dwarf near eps Indi.
resolve_target("eps Ind B")            -> ra 331.0441, dec -56.7828, pm (3982, -2467) mas/yr
search_observations(331.0441, -56.7828) -> 458 frames, 4 visits
build_sequence(..., fov_arcmin=8)       -> sequence_id ...; sequence_status -> done
get_blink_strip(id, band="ALL")         -> [image: a source shifts between panels near the centre]
analyze_field(id) ... job_result(job)   -> movers: 2 (both matched to Gaia, total motion ~4.7"/yr), red: 1
analyze_point(id, ra, dec)              -> class: T dwarf, CH4 3.3 um index 0.19, motion consistent with Gaia
```

`scripts/mcp_discovery_demo.py <url>/mcp --field "eps Ind B" --field "200 45"` runs this loop over HTTP and
writes `report.json` plus the blink strips it looked at.
