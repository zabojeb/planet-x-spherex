/** Verification strip: a PSF fit and an 11×11 cutout in every exposure near the candidate (POST .../verify),
 *  static ("stare") or moved along a trial motion ("track"), with the fitted S/N under each cutout. */
import { useEffect, useRef, useState } from "react";
import { verifyPosition } from "../../api";
import { formatDate } from "../../astro";
import type { VerifyResult } from "../../types";

export interface VerifySubject {
  key: string;
  label: string;
  ra: number;
  dec: number;
  rate?: number;
  pa?: number;
  epoch?: number;
  mjd_ref?: number;
}

function Cutout({ pixels }: { pixels: (number | null)[][] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    const h = pixels.length;
    const w = pixels[0]?.length ?? 0;
    const vals = pixels.flat().filter((v): v is number => v != null && Number.isFinite(v)).sort((a, b) => a - b);
    const lo = vals[Math.floor(vals.length * 0.05)] ?? 0;
    const hi = vals[Math.floor(vals.length * 0.995)] ?? 1;
    const img = ctx.createImageData(w, h);
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const v = pixels[h - 1 - y][x]; // row 0 = south: flip so north is up
        const o = (y * w + x) * 4;
        if (v == null || !Number.isFinite(v)) {
          img.data.set([38, 44, 54, 255], o);
        } else {
          const t = Math.asinh(Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1))) * 8) / Math.asinh(8);
          const g = Math.round(t * 255);
          img.data.set([g, g, g, 255], o);
        }
      }
    cv.width = w;
    cv.height = h;
    ctx.putImageData(img, 0, 0);
  }, [pixels]);
  return <canvas ref={ref} className="cutout" />;
}

export function VerifyPanel({ seqId, subject }: { seqId: string; subject: VerifySubject }) {
  const [st, setSt] = useState<{ key: string; res?: VerifyResult; error?: string }>({ key: "" });
  const [mode, setMode] = useState<"stare" | "track">(subject.rate ? "track" : "stare");
  useEffect(() => {
    const ctrl = new AbortController();
    setSt({ key: subject.key });
    verifyPosition(seqId, { ra: subject.ra, dec: subject.dec, rate: subject.rate ?? 0, pa: subject.pa ?? 0, epoch: subject.epoch, mjd_ref: subject.mjd_ref }, ctrl.signal)
      .then((res) => setSt({ key: subject.key, res }))
      .catch((e) => !ctrl.signal.aborted && setSt({ key: subject.key, error: String(e?.message || e) }));
    return () => ctrl.abort();
  }, [subject.key, seqId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (st.key !== subject.key || (!st.res && !st.error)) return <div className="skeleton" style={{ height: 160 }} aria-label="Fitting every exposure" />;
  if (st.error) return <p className="chart-empty static">{st.error}</p>;
  const r = st.res!;
  const strips = mode === "track" && r.strips.track.length ? r.strips.track : r.strips.stare;
  const byIndex = new Map(r.exposures.map((e) => [e.index, e]));
  const s = r.summary;
  return (
    <div className="verify">
      <p className="small">
        <strong>{subject.label}</strong> · detected at S/N ≥ 3 in <span className="num">{s.detected_snr3}</span> of {s.fitted} fitted exposures
        {s.median_snr != null && <> · median S/N <span className="num">{s.median_snr.toFixed(1)}</span></>}
        {s.median_offset_arcsec != null && <> · median offset <span className="num">{s.median_offset_arcsec.toFixed(1)}″</span></>}
      </p>
      {r.strips.track.length > 0 && (
        <div className="verify-modes" role="radiogroup" aria-label="Cutouts">
          {(["stare", "track"] as const).map((m) => (
            <button key={m} role="radio" aria-checked={mode === m} className={`chip${mode === m ? " on" : ""}`} onClick={() => setMode(m)}>
              {m === "stare" ? "Fixed position" : "Along the motion"}
            </button>
          ))}
        </div>
      )}
      <div className="verify-strip">
        {strips.map((c) => {
          const e = byIndex.get(c.index);
          const snr = e?.fit?.snr;
          return (
            <figure key={c.index} className={snr != null && snr >= 3 ? "det" : ""} title={e ? `${formatDate(e.mjd, true)} · ${e.band} ${e.wl.toFixed(3)} µm` : undefined}>
              <Cutout pixels={c.pixels} />
              <figcaption className="num">
                {e?.band ?? ""} {snr != null ? snr.toFixed(1) : "—"}
              </figcaption>
            </figure>
          );
        })}
      </div>
      <p className="fine">Each cutout is {2 * r.strips.half_size_px + 1}×{2 * r.strips.half_size_px + 1} px, north up; the number is the S/N of a PSF fit with free position. Outlined: S/N ≥ 3.</p>
    </div>
  );
}
