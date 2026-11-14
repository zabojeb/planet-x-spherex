/** Presentation helpers shared by the spectrum and light-curve charts. */
import { formatNumber } from "./components/chart/ChartFrame";
import type { Photometry } from "./types";

export function measurementLines(p: Photometry): string[] {
  const out: string[] = [];
  const method = p.method === "psf" ? "PSF fit" : p.method === "aperture" ? "aperture" : p.method === "ring" ? "ring around the saturated core" : null;
  const extra = [method, p.chi2 != null ? `χ² ${p.chi2.toFixed(1)}` : null, p.npix != null ? `${p.npix} px` : null]
    .filter(Boolean)
    .join(" · ");
  if (extra) out.push(extra);
  if (p.flux_ap_mjy != null && p.method === "psf") {
    out.push(`aperture: ${formatNumber(p.flux_ap_mjy)} ± ${formatNumber(p.err_ap_mjy ?? 0)} mJy`);
  }
  if (p.relative) out.push("Relative flux only: proportional to the total, not calibrated");
  else if (p.saturated) out.push("Saturated: core pixels excluded, flux from the PSF wings");
  else if (p.flagged) out.push("Flagged: artifacts or masked pixels at the source");
  else out.push("Clean measurement");
  return out;
}

export interface QualityShares {
  flagged: number;
  saturated: number;
}

export function qualityShares(ps: Photometry[]): QualityShares {
  if (!ps.length) return { flagged: 0, saturated: 0 };
  return {
    flagged: ps.filter((p) => p.flagged && !p.saturated).length / ps.length,
    saturated: ps.filter((p) => p.saturated).length / ps.length,
  };
}

export function qualityNote(q: QualityShares, what: string): string | null {
  if (q.saturated > 0.5) {
    return `${Math.round(q.saturated * 100)}% of ${what} are saturated: the bright core was excluded and fluxes come from the PSF wings, so expect larger errors.`;
  }
  if (q.flagged > 0.6) {
    return `${Math.round(q.flagged * 100)}% of ${what} are flagged (artifacts or masked pixels at the source), so treat them with care.`;
  }
  return null;
}

/** Calibrated measurements for absolute plots (relative-only points are excluded). */
export const absolute = (ps: Photometry[]) => ps.filter((p) => !p.relative);
