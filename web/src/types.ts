/** Detector id as sent by the API ("D1".."D6"). Band order and ranges always come from meta.bands. */
export type BandId = string;

export interface BandInfo {
  id: BandId;
  wl_min: number;
  wl_max: number;
  R: number;
}

export interface Target {
  id?: string | null;
  name?: string | null;
  ra: number;
  dec: number;
  pmra: number;
  pmdec: number;
  ref_epoch: number;
  parallax?: number | null;
  source?: string;
  description?: string | null;
  fov_arcmin?: number | null;
  /** Optional display hints from the API (e.g. a target only visible at long wavelengths). */
  default_band?: string | null;
  default_mode?: string | null;
}

export interface TrackPoint {
  epoch: number;
  ra: number;
  dec: number;
  x: number;
  y: number;
}

export interface EpochBand {
  n: number;
  wl_min: number;
  wl_max: number;
  mjd_mean: number;
  noise: number | null;
}

export interface Epoch {
  index: number;
  n: number;
  mjd_mean: number;
  mjd_min: number;
  mjd_max: number;
  date_min: string;
  date_max: string;
  bands: Record<BandId, EpochBand | undefined>;
  /** A short partial visit (few exposures): skipped by the player unless asked for. */
  partial?: boolean;
}

export interface Exposure {
  index: number;
  epoch: number;
  band: BandId;
  mjd: number;
  date: string;
  wl: number;
  dwl: number;
  rms: number | null;
  psf_fwhm: number;
  obs_id: string;
  collection: string;
  file: string;
  flag_counts: Record<string, number>;
  target_px: [number, number];
}

export interface Photometry {
  index: number;
  epoch: number;
  band: BandId;
  mjd: number;
  wl: number;
  dwl: number;
  flux_mjy: number | null;
  err_mjy: number | null;
  ab_mag: number | null;
  flagged: boolean;
  ap_flags: number;
  masked_frac: number | null;
  method?: "psf" | "aperture" | "ring";
  /** Flux only proportional to the true total (e.g. ring photometry of a saturated star): not calibrated. */
  relative?: boolean;
  /** Core pixels excluded (saturated); the flux comes from the PSF wings. */
  saturated?: boolean;
  npix?: number | null;
  chi2?: number | null;
  flux_ap_mjy?: number | null;
  err_ap_mjy?: number | null;
}

export interface FlagBit {
  bit: number;
  name: string;
  description: string;
  masked: boolean;
  phot_bad: boolean;
}

export interface ArraySpec {
  file: string;
  dtype: string;
  shape: number[];
  unit?: string;
}

export interface Grid {
  ra: number;
  dec: number;
  n: number;
  pixscale_arcsec: number;
  fov_arcmin: number;
}

export interface Meta {
  id: string;
  version: number;
  created: string;
  params: {
    ra: number;
    dec: number;
    pmra: number;
    pmdec: number;
    ref_epoch: number;
    name: string | null;
    fov_arcmin: number;
    gap_days: number;
    max_epoch_days: number;
  };
  target: Target & { track: TrackPoint[] };
  grid: Grid;
  bands: BandInfo[];
  epochs: Epoch[];
  arrays: { coadds: ArraySpec; coverage: ArraySpec; flags: ArraySpec; exposures: ArraySpec | null };
  exposures: Exposure[];
  photometry: Photometry[];
  flag_bits: FlagBit[];
  /** Present on lite meta: the per-exposure tables are fetched separately, as columns. */
  tables?: Record<"exposures" | "photometry", { rows: number; url: string }>;
  stats: {
    frames_found: number;
    frames_used: number;
    errors: string[];
    requests?: number;
    bytes?: number;
    cache_hits?: number;
    seconds?: Record<string, number | null>;
  };
  sources: { archive: string; collections: string[]; data: string };
}

export interface Job {
  id: string;
  status: "queued" | "running" | "done" | "error";
  stage: string;
  done: number;
  total: number;
  error: string | null;
  elapsed: number;
  /** Builds that start before this one while it is queued (null otherwise). */
  position?: number | null;
}

export interface CatalogSource {
  id: string;
  ra: number;
  dec: number;
  epoch: number;
  pmra: number | null;
  pmdec: number | null;
  mag: number | null;
  mag_band: string;
  [extra: string]: unknown;
}

export type ArrayName = "coadds" | "coverage" | "flags";
export type PlaneArray = Float32Array | Uint16Array | Uint32Array;

/** Read access to the [n, n] planes of a sequence that have been downloaded so far. */
export interface PlaneSource {
  plane(array: "coadds", e: number, b: BandId): Float32Array | undefined;
  plane(array: "coverage", e: number, b: BandId): Uint16Array | undefined;
  plane(array: "flags", e: number, b: BandId): Uint32Array | undefined;
  plane(array: ArrayName, e: number, b: BandId): PlaneArray | undefined;
}

export interface PlaneKey {
  array: ArrayName;
  e: number;
  b: BandId;
}

/** meta may be "lite": then exposures/photometry are empty until loaded with fetchTable. */
export interface SequenceData {
  meta: Meta;
  lite: boolean;
  planes: PlaneSource;
}

export interface PeriodogramResult {
  method: string;
  band: string | null;
  n_points: number;
  baseline_days: number;
  min_period_days: number;
  max_period_days: number;
  best: { period_days: number; frequency_per_day: number; power: number; false_alarm_probability: number | null };
  peaks: { period_days: number; power: number }[];
  periodogram: { period_days: number[]; power: number[] };
  t0_mjd: number;
  folded: { index: number; mjd: number; phase: number; rel: number; rel_err: number | null; band: BandId; wl: number; flagged: boolean; relative?: boolean }[];
  model: { phase: number[]; rel?: number[]; rel_by_band?: Record<string, number[]> };
}

export interface SourcePhotometry {
  sequence: string;
  target: { ra: number; dec: number; pmra: number; pmdec: number; ref_epoch: number; name?: string | null };
  photometry: Photometry[];
  stats?: Record<string, number>;
}

export interface ExposureStack {
  data: Float32Array;
  indices: number[];
}
