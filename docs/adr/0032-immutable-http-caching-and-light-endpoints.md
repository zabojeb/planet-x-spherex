# ADR-0032: Immutable HTTP caching, precompressed metadata and lightweight result endpoints

- **Status:** Accepted
- **Date:** 2026-11-14

## Context

The sequence id is a hash of the parameters and the algorithm version, so a finished result never changes. The web client used to download the full `meta.json` (R Dra: 2 MB raw, 350 kB gzipped) and the whole co-add cube even when it showed one epoch and one detector.

## Decision

- Every finished-result URL is served with `Cache-Control: public, max-age=31536000, immutable` and an ETag; `If-None-Match` returns 304. Browsers and any CDN in front keep results forever.
- `meta.json` is precompressed at write time (`meta.json.br`, `meta.json.gz`) and served with `Content-Encoding` by Accept-Encoding; older results get the variants on first request.
- New additive endpoints (the existing ones are unchanged):
  - `GET /api/sequences/{id}/lite`: meta without the `exposures` and `photometry` tables, plus `tables.{name}.rows` and `.url`;
  - `GET /api/sequences/{id}/columns/{exposures|photometry}`: the table as columns `{rows, columns, data: {col: [...]}}`;
  - `GET /api/sequences/{id}/{coadds|coverage|flags}.bin?epoch=&band=`: one `[n, n]` plane in the cube's dtype, with `X-Array-Shape` and `X-Array-Dtype`; the whole-cube URL also supports HTTP `Range`.
- Catalog layers are fetched separately per catalog, cached for an hour with a content ETag.
- The built client: hashed files under `assets/` are immutable, `index.html` is `no-cache`.

## Consequences

- A repeat visit costs a few 304s or nothing; a first visit to one detector downloads one plane instead of the cube.
- Gzip middleware skips responses that already carry `Content-Encoding`.

## Alternatives considered

- **Presigned bucket URLs for the arrays:** offloads bandwidth from the API, but needs bucket CORS and leaks expiring URLs into shareable state; API replicas serve local copies fast enough for now.
