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
  /** A solar-system object: the sequence is built co-moving with it (stars trail). */
  moving?: MovingRequest | null;
}

/** The co-moving fields of a sequence request (from /api/moving/search's suggested_request). */
export interface MovingRequest {
  object_name: string;
  object_type?: string | null;
  naif_id?: number | null;
  obs_begin?: string | null;
  obs_end?: string | null;
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
  /** "comoving" when every exposure was resampled on a grid centred on a moving object. */
  frame?: string;
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
  /** Set on a preview: an early, complete result of part of the exposures (the build `of` is still running). */
  preview?: { of: string; n: number; frames: number; total: number };
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
  /** Id of the latest preview of a running build (a finished result of the exposures read so far). */
  preview?: string | null;
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

// ------------------------------------------------------------------ archival epochs (2MASS, AllWISE, NEOWISE)
export interface ArchivalFrame {
  index: number;
  survey: string;
  survey_id: string;
  band: string;
  label: string;
  wl_um: number;
  mjd: number;
  mjd_min: number;
  mjd_max: number;
  date: string;
  epoch_year: number;
  n_images: number;
  coverage: number;
  target_px: [number, number] | null;
  target_in_field: boolean;
}

export interface ArchivalMeta {
  sequence: string;
  grid: Grid;
  surveys: string[];
  frames: ArchivalFrame[];
  array: { shape: number[]; unit?: string };
  stats?: { errors?: string[] };
}

// ------------------------------------------------------------------ solar-system objects
export interface MovingSearch {
  object: string;
  n_exposures: number;
  date_min: string;
  date_max: string;
  ephemeris: [number, number, number][];
  suggested_request: { ra: number; dec: number; name: string; fov_arcmin: number } & MovingRequest;
}

export interface MovingChoice {
  name: string;
  type?: string;
  naif_id: number;
  designation?: string;
}

// ------------------------------------------------------------------ spectral analysis
export interface SpecBin {
  band: BandId;
  bin: number;
  wl: number;
  wl_lo: number;
  wl_hi: number;
  flux_mjy: number;
  err_mjy: number | null;
  n: number;
  n_rejected?: number;
  chi2?: number | null;
  relative: boolean;
  epochs?: number[];
  continuum_mjy?: number | null;
}

export interface SpecFeature {
  id: string;
  name: string;
  species: string;
  kind: "absorption" | "emission" | string;
  center: number;
  band: [number, number];
  depth: number | null;
  depth_err: number | null;
  ew_um?: number | null;
  significance: number | null;
  detected: boolean;
}

export interface SpecIndex {
  value: number | null;
  err: number | null;
  numerator_um?: [number, number];
  denominator_um?: [number, number];
  description?: string;
}

export interface SpectrumResult {
  version?: number;
  target?: string;
  n_measurements: number;
  bins: SpecBin[];
  relative_bins: SpecBin[];
  features: SpecFeature[];
  detections?: { kind: string; wl_min: number; wl_max: number; wl_center: number; significance: number; identified_as: string[] }[];
  continuum?: { available: boolean; reliable: boolean; note?: string };
  indices: Record<string, SpecIndex>;
  notes?: string[];
}

export interface TemplateFit {
  name: string;
  sptype: string;
  family: string;
  source: string;
  chi2_red: number;
  probability: number;
  n_bins: number;
}

export interface SynthMag {
  coverage: number;
  extrapolated: boolean;
  flux_mjy: number | null;
  mag: number | null;
  mag_err: number | null;
}

export interface ClassifyResult {
  available: boolean;
  reason?: string;
  summary?: { label: string | null; family: string | null; notes: string[] };
  templates?: { best: TemplateFit | null; ranked: TemplateFit[]; class_probabilities?: Record<string, number>; max_wl_um?: number };
  template_error?: string | null;
  blackbody?: { teff_k: number; teff_range_k?: [number, number]; chi2_red: number } | null;
  ir_index?: { alpha: number; alpha_err: number; lada_class: string; note?: string } | null;
  model_curves?: Record<string, { source?: string; wl: number[]; flux_mjy: (number | null)[] }>;
  synthetic_photometry: Record<string, SynthMag>;
  catalog_comparison: {
    bands: Record<string, { synthetic: number | null; catalog: number | null; delta_mag: number | null; suspect?: boolean; catalog_id?: string }>;
    errors?: unknown;
  } | null;
  notes?: string[];
}

export interface VariabilityResult {
  available: boolean;
  reason?: string;
  reference_epoch?: number;
  systematic?: number;
  epochs: {
    epoch: number;
    variable?: boolean;
    significant_bands?: string[];
    chi2_red_constant?: number | null;
    per_band?: Record<string, { ratio: number; err: number; z: number; n_bins: number; kind: string }>;
    ratios?: { band: BandId; bin: number; wl: number; kind: string; ratio: number; err: number; z: number }[];
  }[];
}

export interface SpectralAnalysis {
  spectrum: SpectrumResult | null;
  classification: ClassifyResult | null;
  variability: VariabilityResult | null;
}

// ------------------------------------------------------------------ point analysis (POST /api/sequences/{id}/analyze)
/** Any slot of an analysis may fail on its own. */
export type Slot<T> = T | { error: string } | null | undefined;

export interface Finding {
  text: string;
  evidence?: string | string[] | null;
  confidence: number | string; // 0–1, or "high" | "medium" | "low"
}

export interface CatalogMatch {
  catalog?: string;
  id: string | number;
  name?: string | null;
  type?: string | null;
  mag?: number | null;
  mag_band?: string | null;
  sep_arcsec?: number | null;
  pmra?: number | null;
  pmdec?: number | null;
}

export interface PointAnalysis {
  id?: string;
  point?: { ra: number; dec: number; x?: number; y?: number; name?: string | null };
  verdict?: Slot<{ summary?: string; findings: Finding[]; novelty?: number | string | null; novelty_note?: string | null }>;
  identification?: Slot<{ matches?: CatalogMatch[]; simbad?: CatalogMatch[]; solar_system?: { name: string; kind?: string; vmag?: number | null; sep_arcsec?: number | null }[] }>;
  photometry?: Slot<{ n?: number; records?: Photometry[]; summary?: Record<string, unknown> }>;
  spectrum?: Slot<SpectrumResult>;
  classification?: Slot<ClassifyResult>;
  variability?: Slot<VariabilityResult>;
  motion?: Slot<{ pmra: number; pmdec: number; pmra_err?: number; pmdec_err?: number; pm_total?: number; significance?: number; catalog?: { catalog: string; pmra: number; pmdec: number } | null; discrepancy_sigma?: number | null }>;
  periodogram?: Slot<PeriodogramResult>;
  red_excess?: Slot<{ ratio_long_short?: number; excess_snr?: number; note?: string }>;
  tracklets?: Slot<{ items?: { id: string; epoch: number; n: number; rate_arcsec_per_hr: number; pa_deg: number; sep_arcsec?: number; known?: string | null }[] }>;
}

// ------------------------------------------------------------------ field analysis
export interface FieldMover {
  id: string; x: number; y: number; ra: number; dec: number; category: string; score: number;
  motion?: { pmra: number; pmdec: number; pm_total: number; significance?: number } | null;
  catalog_motion?: { catalog: string; id: string; pmra: number; pmdec: number } | null;
  catalog_discrepancy_sigma?: number | null;
  track?: { epoch: number; x: number; y: number }[];
}
export interface FieldTracklet {
  id: string; epoch: number; n: number; mjd_mid: number; rate_arcsec_per_hr: number; pa_deg: number; rms_arcsec?: number; mean_snr: number; mean_rb?: number; score: number;
  detections: { exposure: number; mjd: number; band: string; x: number; y: number; ra: number; dec: number; snr: number }[];
  known?: { name: string; kind?: string; vmag?: number } | null;
}
export interface FieldVariable { id: string; x: number; y: number; ra: number; dec: number; method?: string; n?: number; chi2_red: number; amplitude: number; score: number; period?: { best_period_days: number } | null }
export interface FieldRed { id: string; x: number; y: number; ra: number; dec: number; ratio_long_short?: number; snr_long?: number; no_gaia?: boolean; score: number }
export interface FieldAnomaly { id: string; x: number; y: number; ra: number; dec: number; score: number; no_gaia?: boolean }
export interface FieldResult {
  id: string;
  sequence: string;
  candidates: { movers?: FieldMover[]; faint_movers?: FieldMover[]; tracklets?: FieldTracklet[]; pairs?: FieldTracklet[]; variables?: FieldVariable[]; red?: FieldRed[]; anomalies?: FieldAnomaly[] };
  stats?: { sources?: number; seconds?: number };
  notes?: string[];
}
export interface KnownObject { name: string; kind?: string; mjd: number; ra: number; dec: number; vmag?: number | null; ra_rate?: number; dec_rate?: number; x: number; y: number; inside: boolean; speed_arcsec_per_hr?: number; pa_deg?: number }
export interface KnownObjects { sequence: string; times: { mjd: number; objects: KnownObject[] }[] }
export interface StackSearch {
  epoch: number;
  candidates: { id: string; x: number; y: number; ra: number; dec: number; snr: number; rate_arcsec_per_hr: number; pa_deg: number; sub_snr?: number[]; score: number }[];
  trials?: number;
}
export interface VerifyResult {
  summary: { exposures: number; fitted: number; detected_snr3: number; median_snr: number | null; median_offset_arcsec?: number | null };
  exposures: { index: number; mjd: number; band: string; wl: number; fit: { snr: number; flux_mjy: number; offset_arcsec?: number } | null }[];
  strips: { half_size_px: number; stare: { index: number; pixels: (number | null)[][] }[]; track: { index: number; pixels: (number | null)[][] }[] };
}
