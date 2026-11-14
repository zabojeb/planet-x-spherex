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

export interface SequenceData {
  meta: Meta;
  coadds: Float32Array;
  coverage: Uint16Array;
  flags: Uint32Array;
}

export interface ExposureStack {
  data: Float32Array;
  indices: number[];
}
