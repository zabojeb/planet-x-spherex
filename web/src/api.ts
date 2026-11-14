import type { BandId, CatalogSource, ExposureStack, Job, Meta, SequenceData, Target } from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  constructor(message: string, public status = 0) {
    super(message);
  }
}

async function request(path: string, init?: RequestInit, retries = 2): Promise<Response> {
  let last: unknown;
  for (let i = 0; i <= retries; i++) {
    try {
      const r = await fetch(BASE + path, init);
      if (r.status === 502 || r.status === 503 || r.status === 504) throw new ApiError(`server busy (${r.status})`, r.status);
      return r;
    } catch (e) {
      last = e;
      if (init?.signal?.aborted) throw e;
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

export type Progress = { stage: string; done: number; total: number; elapsed: number };

async function startSequence(req: SequenceRequest, signal?: AbortSignal): Promise<Job> {
  const r = await request("/api/sequences", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  return r.json();
}

async function pollSequence(id: string, signal?: AbortSignal): Promise<{ meta?: Meta; job?: Job }> {
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

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("aborted", "AbortError"));
    });
  });

/** Start (or reuse) a server-side build, follow its progress and download the arrays. */
export async function loadSequence(
  req: SequenceRequest,
  onProgress: (p: Progress) => void,
  signal?: AbortSignal,
): Promise<SequenceData> {
  const job = await startSequence(req, signal);
  let meta: Meta | undefined;
  onProgress({ stage: job.stage, done: job.done, total: job.total, elapsed: job.elapsed });
  for (;;) {
    const res = await pollSequence(job.id, signal);
    if (res.meta) {
      meta = res.meta;
      break;
    }
    if (res.job) onProgress({ stage: res.job.stage, done: res.job.done, total: res.job.total, elapsed: res.job.elapsed });
    await sleep(900, signal);
  }
  onProgress({ stage: "download", done: 0, total: 3, elapsed: 0 });
  const [c, v, f] = await Promise.all([
    fetchArray(meta.id, "coadds", signal),
    fetchArray(meta.id, "coverage", signal),
    fetchArray(meta.id, "flags", signal),
  ]);
  return { meta, coadds: new Float32Array(c), coverage: new Uint16Array(v), flags: new Uint32Array(f) };
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

export async function fetchCatalog(name: string, ra: number, dec: number, radiusArcmin: number): Promise<CatalogSource[]> {
  const q = `ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&radius_arcmin=${radiusArcmin.toFixed(2)}`;
  const r = await request(`/api/catalogs/${name}?${q}`, undefined, 1);
  if (!r.ok) throw new ApiError(await detail(r), r.status);
  return r.json();
}
