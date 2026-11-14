/**
 * The only module that talks to the API. Built to be cheap for a shared server:
 * - one AbortSignal per load cancels polls and downloads; an abandoned job is withdrawn with DELETE;
 * - polling doubles as a heartbeat (1 s while visible, 10 s while the tab is hidden);
 * - 429 + Retry-After becomes a calm "queued" state instead of an error;
 * - decoded sequences live in an in-memory LRU, so toggles and revisits never refetch;
 * - meta and arrays download in parallel as soon as the job is done.
 */
import type { BandId, CatalogSource, ExposureStack, Job, Meta, SequenceData, Target } from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
const POLL_VISIBLE_MS = 1000;
const POLL_HIDDEN_MS = 10000;
const LRU_SIZE = 8;

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

async function request(path: string, init?: RequestInit, retries = 2): Promise<Response> {
  let last: unknown;
  for (let i = 0; i <= retries; i++) {
    try {
      const r = await fetch(BASE + path, init);
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

export async function getTargets(): Promise<Target[]> {
  const r = await request("/api/targets");
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  return r.json();
}

export async function resolveQuery(q: string): Promise<Target> {
  const r = await request(`/api/resolve?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  return r.json();
}

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

// ------------------------------------------------------------------ in-memory LRU of decoded sequences
const lru = new Map<string, SequenceData>();
const idForRequest = new Map<string, string>(); // request -> sequence id, so revisits skip the POST
function lruGet(id: string): SequenceData | undefined {
  const v = lru.get(id);
  if (v) {
    lru.delete(id);
    lru.set(id, v);
  }
  return v;
}
function lruPut(data: SequenceData, req?: SequenceRequest): void {
  if (req) idForRequest.set(JSON.stringify(req), data.meta.id);
  lru.delete(data.meta.id);
  lru.set(data.meta.id, data);
  while (lru.size > LRU_SIZE) lru.delete(lru.keys().next().value as string);
}

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

/** Tell the server nobody is waiting for this job any more (best effort, survives page unload). */
export function withdraw(id: string): void {
  try {
    void fetch(`${BASE}/api/sequences/${id}`, { method: "DELETE", keepalive: true }).catch(() => undefined);
  } catch {
    /* ignore */
  }
}

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

async function getStatus(id: string, signal?: AbortSignal): Promise<{ meta?: Meta; job?: Job }> {
  const r = await request(`/api/sequences/${id}`, { signal });
  if (r.status === 200) return { meta: await r.json() };
  if (r.status === 202) return { job: await r.json() };
  if (r.status === 500) {
    const j: Job = await r.json();
    throw new ApiError(j.error || "the server could not build this sequence", 500);
  }
  throw new ApiError(await detail(r), r.status);
}

async function fetchArray(id: string, name: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const r = await request(`/api/sequences/${id}/${name}.bin`, { signal });
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  return r.arrayBuffer();
}

async function downloadArrays(id: string, signal?: AbortSignal) {
  const [c, v, f] = await Promise.all([
    fetchArray(id, "coadds", signal),
    fetchArray(id, "coverage", signal),
    fetchArray(id, "flags", signal),
  ]);
  return { coadds: new Float32Array(c), coverage: new Uint16Array(v), flags: new Uint32Array(f) };
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

/**
 * Start (or reuse) a server-side build, keep it alive with heartbeats, download and decode the arrays.
 * Aborting the signal stops everything and withdraws interest in an unfinished job.
 */
export async function loadSequence(
  req: SequenceRequest,
  onProgress: (p: Progress) => void,
  signal?: AbortSignal,
): Promise<SequenceData> {
  const known = idForRequest.get(JSON.stringify(req));
  const hit = known ? lruGet(known) : undefined;
  if (hit) return hit;
  const job = await withBackoff(() => postSequence(req, signal), onProgress, signal);
  const cached = lruGet(job.id);
  if (cached) {
    idForRequest.set(JSON.stringify(req), job.id);
    return cached;
  }

  let finished = job.status === "done";
  const onHide = () => !finished && withdraw(job.id);
  window.addEventListener("pagehide", onHide);
  const onAbort = () => !finished && withdraw(job.id);
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    let meta: Meta | undefined;
    let arrays: Promise<Awaited<ReturnType<typeof downloadArrays>>> | undefined;
    if (finished) {
      // Cached on the server: metadata and arrays in parallel.
      arrays = downloadArrays(job.id, signal);
    } else {
      onProgress({ stage: job.stage, done: job.done, total: job.total, elapsed: job.elapsed });
    }
    while (!meta) {
      const res = await withBackoff(() => getStatus(job.id, signal), onProgress, signal);
      if (res.meta) meta = res.meta;
      else if (res.job) {
        onProgress({ stage: res.job.stage, done: res.job.done, total: res.job.total, elapsed: res.job.elapsed });
        await heartbeat(signal);
      }
    }
    finished = true;
    if (!arrays) {
      onProgress({ stage: "download", done: 0, total: 0, elapsed: 0 });
      arrays = downloadArrays(meta.id, signal);
    }
    const data: SequenceData = { meta, ...(await arrays) };
    lruPut(data, req);
    return data;
  } finally {
    window.removeEventListener("pagehide", onHide);
    signal?.removeEventListener("abort", onAbort);
  }
}

const prefetching = new Set<string>();

/**
 * Warm the cache for a likely next target (e.g. a hovered preset). Only finished server results are
 * downloaded; if the server would have to build, interest is withdrawn at once so nothing heavy starts.
 */
export async function prefetchSequence(req: SequenceRequest): Promise<void> {
  const key = JSON.stringify(req);
  if (prefetching.has(key)) return;
  prefetching.add(key);
  try {
    const job = await postSequence(req);
    if (lruGet(job.id)) return;
    if (job.status !== "done") {
      withdraw(job.id);
      return;
    }
    const [res, arrays] = await Promise.all([getStatus(job.id), downloadArrays(job.id)]);
    if (res.meta) lruPut({ meta: res.meta, ...arrays }, req);
  } catch {
    /* prefetch is best effort */
  } finally {
    prefetching.delete(key);
  }
}

export async function fetchExposures(id: string, epoch: number, band: BandId, signal?: AbortSignal): Promise<ExposureStack> {
  const r = await request(`/api/sequences/${id}/exposures.bin?epoch=${epoch}&band=${band}`, { signal });
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  const indices = (r.headers.get("X-Exposure-Indices") || "")
    .split(",")
    .filter(Boolean)
    .map(Number);
  return { data: new Float32Array(await r.arrayBuffer()), indices };
}

export async function fetchCatalog(name: string, ra: number, dec: number, radiusArcmin: number, signal?: AbortSignal): Promise<CatalogSource[]> {
  const q = `ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&radius_arcmin=${radiusArcmin.toFixed(2)}`;
  const r = await request(`/api/catalogs/${name}?${q}`, { signal }, 1);
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  return r.json();
}
