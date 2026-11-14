/**
 * The only module that talks to the API. Built to be cheap for a shared server:
 * - every call carries X-Client-Id (one random id per tab), so the server counts interest per tab;
 * - one AbortSignal per load cancels polls and downloads; an abandoned job is withdrawn with DELETE;
 * - polling doubles as a heartbeat (1 s while visible, 10 s while the tab is hidden);
 * - 429 + Retry-After becomes a calm "queued" state instead of an error; 410 (cancelled) re-submits;
 * - metadata comes "lite" (no per-exposure tables); the tables are fetched as columns only when shown;
 * - images come one [n, n] plane at a time, only for what is on screen; when most of a cube is needed
 *   (e.g. playing every epoch of all detectors) the whole cube is fetched in one request instead;
 * - decoded planes live in an LRU keyed by sequence + array + plane, under a byte budget.
 * Every new endpoint has a fallback to the older full-meta / whole-cube API.
 */
import type {
  ArrayName, BandId, CatalogSource, Exposure, ExposureStack, Job, Meta, PeriodogramResult, Photometry, PlaneArray,
  PlaneKey, PlaneSource, SequenceData, SourcePhotometry, Target,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
const POLL_VISIBLE_MS = 1000;
const POLL_HIDDEN_MS = 10000;
const META_LRU = 12;
const PLANE_BUDGET = 320 * 2 ** 20; // bytes of decoded planes kept in memory
const PLANE_CONCURRENCY = 6;
/** Fetch the whole cube once more than this share of its planes is wanted. */
const CUBE_SHARE = 0.5;

/** One random id per tab (per page load), sent with every request. */
const CLIENT_ID = (() => {
  try {
    return crypto.randomUUID();
  } catch {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
})();

/** What the server turned out to support (undefined = not known yet). */
const caps: { lite?: boolean; planes?: boolean } = {};

export class ApiError extends Error {
  constructor(message: string, public status = 0, public retryAfter = 0) {
    super(message);
  }
}

function retryAfterSeconds(r: Response): number {
  const h = r.headers.get("Retry-After");
  if (!h) return 5;
  const s = Number(h);
  if (Number.isFinite(s)) return Math.min(Math.max(s, 1), 120);
  const t = Date.parse(h);
  return Number.isFinite(t) ? Math.min(Math.max((t - Date.now()) / 1000, 1), 120) : 5;
}

function withClient(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  headers.set("X-Client-Id", CLIENT_ID);
  return { ...init, headers };
}

async function request(path: string, init?: RequestInit, retries = 2): Promise<Response> {
  let last: unknown;
  for (let i = 0; i <= retries; i++) {
    try {
      const r = await fetch(BASE + path, withClient(init));
      if (r.status === 429) throw new ApiError("The server is busy", 429, retryAfterSeconds(r));
      if (r.status === 502 || r.status === 503 || r.status === 504) throw new ApiError(`server busy (${r.status})`, r.status);
      return r;
    } catch (e) {
      if (init?.signal?.aborted || (e instanceof ApiError && e.status === 429)) throw e;
      last = e;
      await new Promise((res) => setTimeout(res, 800 * (i + 1)));
    }
  }
  throw last instanceof Error ? last : new ApiError("network error");
}

async function detail(r: Response): Promise<string> {
  try {
    const j = await r.json();
    return (j && (j.detail || j.error)) || `HTTP ${r.status}`;
  } catch {
    return `HTTP ${r.status}`;
  }
}

async function json<T>(path: string, init?: RequestInit, retries?: number): Promise<T> {
  const r = await request(path, init, retries);
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  return r.json();
}

export const getTargets = () => json<Target[]>("/api/targets");
export const resolveQuery = (q: string) => json<Target>(`/api/resolve?q=${encodeURIComponent(q)}`);

export interface SequenceRequest {
  ra: number;
  dec: number;
  pmra: number;
  pmdec: number;
  ref_epoch: number;
  name: string | null;
  fov_arcmin: number;
  gap_days?: number;
  max_epoch_days?: number;
}

/** stage "busy" = rate-limited by the server; retryIn says when the client tries again (seconds). */
export type Progress = { stage: string; done: number; total: number; elapsed: number; retryIn?: number };

// ------------------------------------------------------------------ helpers
const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("aborted", "AbortError"));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("aborted", "AbortError"));
    }, { once: true });
  });

/** Sleep for the heartbeat interval, waking early when the tab becomes visible again. */
function heartbeat(signal?: AbortSignal): Promise<void> {
  if (!document.hidden) return sleep(POLL_VISIBLE_MS, signal);
  return new Promise<void>((resolve, reject) => {
    const done = () => {
      clearTimeout(t);
      document.removeEventListener("visibilitychange", onVis);
      resolve();
    };
    const onVis = () => !document.hidden && done();
    const t = setTimeout(done, POLL_HIDDEN_MS);
    document.addEventListener("visibilitychange", onVis);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      document.removeEventListener("visibilitychange", onVis);
      reject(new DOMException("aborted", "AbortError"));
    }, { once: true });
  });
}

/** Run fn, turning 429 responses into a visible wait-and-retry ("busy") instead of an error. */
async function withBackoff<T>(fn: () => Promise<T>, onProgress: (p: Progress) => void, signal?: AbortSignal): Promise<T> {
  for (;;) {
    try {
      return await fn();
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 429) throw e;
      for (let s = Math.ceil(e.retryAfter); s > 0; s--) {
        onProgress({ stage: "busy", done: 0, total: 0, elapsed: 0, retryIn: s });
        await sleep(1000, signal);
      }
    }
  }
}

/** Tell the server nobody in this tab is waiting for this job any more (best effort, survives unload). */
export function withdraw(id: string): void {
  try {
    void fetch(`${BASE}/api/sequences/${id}`, withClient({ method: "DELETE", keepalive: true })).catch(() => undefined);
  } catch {
    /* ignore */
  }
}

// ------------------------------------------------------------------ metadata (lite + columns)
const metaLru = new Map<string, SequenceData>();
const idForRequest = new Map<string, string>(); // request -> sequence id, so revisits skip the POST
function metaGet(id: string): SequenceData | undefined {
  const v = metaLru.get(id);
  if (v) {
    metaLru.delete(id);
    metaLru.set(id, v);
  }
  return v;
}
function metaPut(data: SequenceData, req?: SequenceRequest): void {
  if (req) idForRequest.set(JSON.stringify(req), data.meta.id);
  metaLru.delete(data.meta.id);
  metaLru.set(data.meta.id, data);
  while (metaLru.size > META_LRU) metaLru.delete(metaLru.keys().next().value as string);
}

const tableCache = new Map<string, unknown[]>();

/** Columns {data: {col: [...]}} back to row records. */
function rowsFromColumns<T>(c: { rows: number; columns: string[]; data: Record<string, unknown[]> }): T[] {
  const out: Record<string, unknown>[] = new Array(c.rows);
  for (let i = 0; i < c.rows; i++) {
    const r: Record<string, unknown> = {};
    for (const k of c.columns) r[k] = c.data[k]?.[i];
    out[i] = r;
  }
  return out as T[];
}

function sequenceData(meta: Meta, lite: boolean): SequenceData {
  if (!lite) {
    tableCache.set(`${meta.id}|exposures`, meta.exposures);
    tableCache.set(`${meta.id}|photometry`, meta.photometry);
  }
  return {
    meta: lite ? { ...meta, exposures: [], photometry: [] } : meta,
    lite,
    planes: planeSource(meta.id),
  };
}

async function fetchMeta(id: string, signal?: AbortSignal): Promise<SequenceData> {
  if (caps.lite !== false) {
    const r = await request(`/api/sequences/${id}/lite`, { signal });
    if (r.ok) {
      caps.lite = true;
      return sequenceData(await r.json(), true);
    }
    if (r.status !== 404) throw new ApiError(await detail(r), r.status);
    caps.lite = false; // older server: full meta below
  }
  const r = await request(`/api/sequences/${id}`, { signal });
  if (r.status !== 200) throw new ApiError(await detail(r), r.status);
  return sequenceData(await r.json(), false);
}

/** The per-exposure tables of a sequence, fetched as columns once and cached. */
export function fetchTable(seq: SequenceData, name: "exposures", signal?: AbortSignal): Promise<Exposure[]>;
export function fetchTable(seq: SequenceData, name: "photometry", signal?: AbortSignal): Promise<Photometry[]>;
export async function fetchTable(seq: SequenceData, name: "exposures" | "photometry", signal?: AbortSignal): Promise<unknown[]> {
  const key = `${seq.meta.id}|${name}`;
  const hit = tableCache.get(key);
  if (hit) return hit;
  const url = seq.meta.tables?.[name]?.url ?? `/api/sequences/${seq.meta.id}/columns/${name}`;
  const r = await request(url, { signal });
  let rows: unknown[];
  if (r.ok) rows = rowsFromColumns(await r.json());
  else if (r.status === 404) {
    // No columns endpoint: take the table from the full metadata.
    const full = await json<Meta>(`/api/sequences/${seq.meta.id}`, { signal });
    tableCache.set(`${seq.meta.id}|exposures`, full.exposures);
    tableCache.set(`${seq.meta.id}|photometry`, full.photometry);
    rows = full[name];
  } else throw new ApiError(await detail(r), r.status);
  tableCache.set(key, rows);
  return rows;
}

/** Tables already in memory (no request). */
export function cachedTable(id: string, name: "exposures"): Exposure[] | undefined;
export function cachedTable(id: string, name: "photometry"): Photometry[] | undefined;
export function cachedTable(id: string, name: "exposures" | "photometry"): unknown[] | undefined {
  return tableCache.get(`${id}|${name}`);
}

// ------------------------------------------------------------------ jobs
async function postSequence(req: SequenceRequest, signal?: AbortSignal): Promise<Job> {
  const r = await request("/api/sequences", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  return r.json();
}

type Poll = { state: "done" } | { state: "running"; job: Job } | { state: "cancelled" };

async function poll(id: string, signal?: AbortSignal): Promise<Poll> {
  const r = await request(`/api/sequences/${id}`, { signal });
  if (r.status === 200) {
    // Done: the full metadata is in the body, but the lite version is far smaller — stop reading here
    // unless the server has no lite endpoint (then the body is cheap enough to take later from cache).
    if (caps.lite !== false) void r.body?.cancel().catch(() => undefined);
    else void r.arrayBuffer().catch(() => undefined);
    return { state: "done" };
  }
  if (r.status === 202) return { state: "running", job: await r.json() };
  if (r.status === 410) return { state: "cancelled" };
  if (r.status === 500) {
    const j: Job = await r.json();
    throw new ApiError(j.error || "the server could not build this sequence", 500);
  }
  throw new ApiError(await detail(r), r.status);
}

/**
 * Start (or reuse) a server-side build, keep it alive with heartbeats, then fetch the (lite) metadata.
 * Images are fetched separately, per plane, with ensurePlanes. Aborting the signal stops everything and
 * withdraws this tab's interest in an unfinished job.
 */
export async function loadSequence(
  req: SequenceRequest,
  onProgress: (p: Progress) => void,
  signal?: AbortSignal,
): Promise<SequenceData> {
  const known = idForRequest.get(JSON.stringify(req));
  const hit = known ? metaGet(known) : undefined;
  if (hit) return hit;
  let job = await withBackoff(() => postSequence(req, signal), onProgress, signal);
  const cached = metaGet(job.id);
  if (cached) {
    idForRequest.set(JSON.stringify(req), job.id);
    return cached;
  }

  let finished = job.status === "done";
  const id = job.id;
  const onHide = () => !finished && withdraw(id);
  window.addEventListener("pagehide", onHide);
  const onAbort = () => !finished && withdraw(id);
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    if (!finished) {
      onProgress({ stage: job.stage, done: job.done, total: job.total, elapsed: job.elapsed });
      for (;;) {
        const res = await withBackoff(() => poll(id, signal), onProgress, signal);
        if (res.state === "done") break;
        if (res.state === "cancelled") {
          // Cancelled (e.g. everyone left while it was queued): ask again, which restarts it.
          job = await withBackoff(() => postSequence(req, signal), onProgress, signal);
          if (job.status === "done") break;
          onProgress({ stage: job.stage, done: job.done, total: job.total, elapsed: job.elapsed });
        } else onProgress({ stage: res.job.stage, done: res.job.done, total: res.job.total, elapsed: res.job.elapsed });
        await heartbeat(signal);
      }
    }
    finished = true;
    onProgress({ stage: "download", done: 0, total: 0, elapsed: 0 });
    const data = await fetchMeta(id, signal);
    metaPut(data, req);
    return data;
  } finally {
    window.removeEventListener("pagehide", onHide);
    signal?.removeEventListener("abort", onAbort);
  }
}

const prefetching = new Set<string>();

/**
 * Warm the cache for a likely next target (e.g. a hovered preset). Only finished server results are
 * touched (and only their lite metadata); if the server would have to build, interest is withdrawn at once.
 */
export async function prefetchSequence(req: SequenceRequest): Promise<void> {
  const key = JSON.stringify(req);
  if (prefetching.has(key) || idForRequest.has(key)) return;
  prefetching.add(key);
  try {
    const job = await postSequence(req);
    if (metaGet(job.id)) {
      idForRequest.set(key, job.id);
      return;
    }
    if (job.status !== "done") {
      withdraw(job.id);
      return;
    }
    metaPut(await fetchMeta(job.id), req);
  } catch {
    /* prefetch is best effort */
  } finally {
    prefetching.delete(key);
  }
}

// ------------------------------------------------------------------ image planes
const CTOR = { coadds: Float32Array, coverage: Uint16Array, flags: Uint32Array } as const;
const planeLru = new Map<string, PlaneArray>(); // `${id}|${array}|${e}|${band}`
let planeBytes = 0;
const inflight = new Map<string, Promise<void>>(); // plane keys and `${id}|${array}|cube`

const pkey = (id: string, array: ArrayName, e: number, b: BandId) => `${id}|${array}|${e}|${b}`;

function planePut(key: string, a: PlaneArray): void {
  const old = planeLru.get(key);
  if (old) {
    planeBytes -= old.byteLength;
    planeLru.delete(key);
  }
  planeLru.set(key, a);
  planeBytes += a.byteLength;
  while (planeBytes > PLANE_BUDGET && planeLru.size > 1) {
    const [k, v] = planeLru.entries().next().value as [string, PlaneArray];
    planeLru.delete(k);
    planeBytes -= v.byteLength;
  }
}

function planeSource(id: string): PlaneSource {
  return {
    plane(array: ArrayName, e: number, b: BandId) {
      const k = pkey(id, array, e, b);
      const v = planeLru.get(k);
      if (v) {
        planeLru.delete(k); // touch
        planeLru.set(k, v);
      }
      return v;
    },
  } as PlaneSource;
}

function putCube(meta: Meta, array: ArrayName, buf: ArrayBuffer): void {
  const n = meta.grid.n;
  const size = n * n;
  const cube = new CTOR[array](buf);
  const nb = meta.bands.length;
  meta.epochs.forEach((ep, e) => {
    meta.bands.forEach((band, bi) => {
      if (!ep.bands[band.id]) return;
      const off = (e * nb + bi) * size;
      if (off + size <= cube.length) planePut(pkey(meta.id, array, e, band.id), cube.slice(off, off + size));
    });
  });
}

async function fetchCube(meta: Meta, array: ArrayName): Promise<void> {
  const key = `${meta.id}|${array}|cube`;
  let p = inflight.get(key);
  if (!p) {
    p = (async () => {
      const r = await request(`/api/sequences/${meta.id}/${array}.bin`);
      if (!r.ok) throw new ApiError(await detail(r), r.status);
      putCube(meta, array, await r.arrayBuffer());
    })().finally(() => inflight.delete(key));
    inflight.set(key, p);
  }
  return p;
}

async function fetchPlane(meta: Meta, k: PlaneKey): Promise<void> {
  const key = pkey(meta.id, k.array, k.e, k.b);
  let p = inflight.get(key);
  if (!p) {
    p = (async () => {
      const r = await request(`/api/sequences/${meta.id}/${k.array}.bin?epoch=${k.e}&band=${k.b}`);
      const n = meta.grid.n;
      const planeBytesExpected = n * n * CTOR[k.array].BYTES_PER_ELEMENT;
      if (r.status === 404 || r.status === 422 || r.status === 400) {
        caps.planes = false;
        return fetchCube(meta, k.array);
      }
      if (!r.ok) throw new ApiError(await detail(r), r.status);
      const buf = await r.arrayBuffer();
      if (buf.byteLength === planeBytesExpected) {
        caps.planes = true;
        planePut(key, new CTOR[k.array](buf));
      } else {
        // An older server ignores epoch/band and sends the whole cube: keep it all.
        caps.planes = false;
        putCube(meta, k.array, buf);
      }
    })().finally(() => inflight.delete(key));
    inflight.set(key, p);
  }
  return p;
}

let running = 0;
const waiting: (() => void)[] = [];
async function slot<T>(fn: () => Promise<T>): Promise<T> {
  if (running >= PLANE_CONCURRENCY) await new Promise<void>((res) => waiting.push(res));
  running++;
  try {
    return await fn();
  } finally {
    running--;
    waiting.shift()?.();
  }
}

/**
 * Make sure the given planes are in memory, in order (first = most urgent). Per array, when more than
 * half of the cube would be missing-and-wanted, the whole cube is fetched in one request instead.
 * Aborting stops queued requests (ones already sent finish and are cached).
 */
export async function ensurePlanes(
  seq: SequenceData,
  keys: PlaneKey[],
  signal?: AbortSignal,
  onProgress?: (done: number, total: number) => void,
): Promise<void> {
  const meta = seq.meta;
  const missing = keys.filter((k) => !seq.planes.plane(k.array, k.e, k.b));
  if (!missing.length) return;
  const total = meta.epochs.length * meta.bands.length;
  const byArray = new Map<ArrayName, PlaneKey[]>();
  for (const k of missing) byArray.set(k.array, [...(byArray.get(k.array) ?? []), k]);
  const jobs: (() => Promise<void>)[] = [];
  for (const [array, list] of byArray) {
    if (caps.planes === false || list.length > CUBE_SHARE * total) jobs.push(() => fetchCube(meta, array));
    else
      for (const k of list)
        jobs.push(async () => {
          if (seq.planes.plane(k.array, k.e, k.b)) return; // arrived with a cube meanwhile
          return caps.planes === false ? fetchCube(meta, k.array) : fetchPlane(meta, k);
        });
  }
  let done = 0;
  onProgress?.(0, jobs.length);
  const run = (job: () => Promise<void>) =>
    slot(async () => {
      if (signal?.aborted) throw new DOMException("aborted", "AbortError");
      await job();
      onProgress?.(++done, jobs.length);
    });
  // Until we know whether the server serves single planes, send one request first (an older server
  // answers with the whole cube, and six of those in parallel would be wasteful).
  if (caps.planes === undefined && jobs.length > 1) await run(jobs.shift()!);
  await Promise.all(jobs.map(run));
}

// ------------------------------------------------------------------ exposures, catalogs, analysis
export async function fetchExposures(id: string, epoch: number, band: BandId, signal?: AbortSignal): Promise<ExposureStack> {
  const r = await request(`/api/sequences/${id}/exposures.bin?epoch=${epoch}&band=${band}`, { signal });
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  const indices = (r.headers.get("X-Exposure-Indices") || "")
    .split(",")
    .filter(Boolean)
    .map(Number);
  return { data: new Float32Array(await r.arrayBuffer()), indices };
}

export function fetchCatalog(name: string, ra: number, dec: number, radiusArcmin: number, signal?: AbortSignal): Promise<CatalogSource[]> {
  const q = `ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&radius_arcmin=${radiusArcmin.toFixed(2)}`;
  return json<CatalogSource[]>(`/api/catalogs/${name}?${q}`, { signal }, 1);
}

const periodograms = new Map<string, Promise<PeriodogramResult>>();

/** Lomb–Scargle of the target's light curve (422 when there are too few points), or of any photometry
 *  (e.g. a measured catalogue source) when given. Cached per sequence / source. */
export function fetchPeriodogram(id: string, source?: { key: string; photometry: Photometry[] }): Promise<PeriodogramResult> {
  const key = source ? `${id}|${source.key}` : id;
  let p = periodograms.get(key);
  if (!p) {
    p = source
      ? json<PeriodogramResult>("/api/periodogram", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ photometry: source.photometry }),
        }, 0)
      : json<PeriodogramResult>(`/api/sequences/${id}/periodogram`);
    p.catch(() => periodograms.delete(key));
    periodograms.set(key, p);
  }
  return p;
}

const sourcePhot = new Map<string, Promise<SourcePhotometry>>();

/** Forced photometry of any (optionally moving) position in every exposure of the sequence. */
export function measureSource(
  id: string,
  src: { ra: number; dec: number; pmra: number; pmdec: number; ref_epoch: number; name?: string | null },
  signal?: AbortSignal,
): Promise<SourcePhotometry> {
  const body = { ra: src.ra, dec: src.dec, pmra: src.pmra, pmdec: src.pmdec, ref_epoch: src.ref_epoch, name: src.name ?? null };
  const key = `${id}|${JSON.stringify(body)}`;
  let p = sourcePhot.get(key);
  if (!p) {
    p = json<SourcePhotometry>(`/api/sequences/${id}/photometry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    }, 0);
    p.catch(() => sourcePhot.delete(key));
    sourcePhot.set(key, p);
  }
  return p;
}

export type ExportKind = "coadds.fits" | "photometry.csv" | "photometry.vot" | "download.sh";

/** Download links (plain navigations, served as attachments). */
export const exportUrl = (id: string, kind: ExportKind) => `${BASE}/api/sequences/${id}/export/${kind}`;
