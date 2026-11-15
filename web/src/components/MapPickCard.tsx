/** Card for a position picked on the zoomed-out sky map (ADR-0061): where it is, what SPHEREx has there, and the
 *  two things to do with it — open the SPHEREx field there, or open it and analyse the point straight away. */
import { ScanSearch, Telescope, X } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchCoverage, type Coverage } from "../api";
import { formatDec, formatDeg, formatRA, toGalactic } from "../astro";

export function MapPickCard(p: {
  ra: number;
  dec: number;
  name?: string | null;
  fov: number;
  research: boolean;
  onOpen: () => void;
  onAnalyze: () => void;
  onClose: () => void;
}) {
  const [cov, setCov] = useState<Coverage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setCov(null);
    setErr(null);
    fetchCoverage(p.ra, p.dec) // shared and cached per position: a card that moves on ignores the answer
      .then((c) => live && setCov(c))
      .catch((e) => live && setErr(String(e?.message || e)));
    return () => {
      live = false;
    };
  }, [p.ra, p.dec]);
  const [l, b] = toGalactic(p.ra, p.dec);
  const none = cov && cov.n_exposures === 0;
  return (
    <div className="map-card">
      <div className="pick-head">
        <span className="micro">{p.name || "Point on the sky"}</span>
        <button className="btn ghost icon" onClick={p.onClose} title="Close" aria-label="Close">
          <X size={14} />
        </button>
      </div>
      <div className="map-card-pos mono">
        <span>α {formatRA(p.ra)}</span>
        <span>δ {formatDec(p.dec)}</span>
      </div>
      {p.research && (
        <div className="map-card-sub mono">
          {formatDeg(p.ra, 5)} {formatDeg(p.dec, 5, true)} · l {formatDeg(l, 2)} b {formatDeg(b, 2, true)}
        </div>
      )}
      <div className="map-card-cov">
        {!cov && !err && <span className="muted small">Looking up SPHEREx coverage…</span>}
        {err && <span className="muted small">Coverage unavailable ({err})</span>}
        {cov && none && <span className="small">No SPHEREx images here yet.</span>}
        {cov && !none && (
          <>
            <span className="small">
              <b>{cov.n_exposures}</b> exposures in <b>{cov.n_visits}</b> visit{cov.n_visits === 1 ? "" : "s"}
              {cov.date_min && cov.date_max ? ` · ${cov.date_min.slice(0, 7)} → ${cov.date_max.slice(0, 7)}` : ""}
            </span>
            {cov.deep && <span className="tag">deep field</span>}
            {p.research && (
              <span className="mono map-card-det">
                {Object.entries(cov.per_detector)
                  .filter(([, n]) => n > 0)
                  .map(([d, n]) => `${d} ${n}`)
                  .join(" · ")}
              </span>
            )}
          </>
        )}
      </div>
      <div className="map-card-actions">
        <button className="btn primary" onClick={p.onOpen} disabled={!!none} title={`Build the SPHEREx field here (${p.fov}′)`}>
          <Telescope size={14} /> Open SPHEREx here
        </button>
        <button className="btn" onClick={p.onAnalyze} disabled={!!none} title="Open the field here and run the full analysis of this point">
          <ScanSearch size={14} /> Analyze
        </button>
      </div>
    </div>
  );
}
