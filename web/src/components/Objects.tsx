/** "Objects in view": every catalogue source inside the field at the shown date, searchable and sortable;
 *  hovering a row highlights it on the sky, clicking flies there and opens its card. */
import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { formatAngle, propagate, separation, totalPm, worldToPixel } from "../astro";
import { CATALOG_IDS, CATALOG_STYLE, type CatalogId } from "../colors";
import type { Overlay } from "../state";
import type { CatalogSource, Grid } from "../types";
import type { CatalogStatus } from "./Controls";
import { Segmented } from "./ui/primitives";

type Sort = "notable" | "near" | "bright" | "fast";
const ROWS = 150;

interface Row {
  cat: CatalogId;
  src: CatalogSource;
  key: string;
  sep: number | null; // deg from the target
  pm: number;
  text: string;
}

export function ObjectsPanel(p: {
  grid: Grid;
  catalogs: Record<string, CatalogSource[]>;
  status: CatalogStatus;
  overlays: Overlay[];
  onToggle: (cat: CatalogId) => void;
  year: number;
  targetWorld: [number, number] | null;
  onPick: (cat: string, src: CatalogSource) => void;
  onHover: (key: string | null) => void;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("notable");
  const [movers, setMovers] = useState(false);
  const on = CATALOG_IDS.filter((c) => p.overlays.includes(c));

  const rows = useMemo(() => {
    const out: Row[] = [];
    const n = p.grid.n;
    for (const cat of on) {
      for (const src of p.catalogs[cat] ?? []) {
        const [ra, dec] = propagate(src, p.year);
        const [x, y] = worldToPixel(p.grid, ra, dec);
        if (x < -0.5 || y < -0.5 || x > n - 0.5 || y > n - 0.5) continue;
        const text = [src.id, src.type, src.otype, src.sp_type].filter(Boolean).join(" ").toLowerCase();
        out.push({ cat, src, key: `${cat}:${src.id}`, sep: p.targetWorld ? separation(...p.targetWorld, ra, dec) : null, pm: totalPm(src), text });
      }
    }
    return out;
  }, [p.catalogs, p.grid, p.year, p.targetWorld, on.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => {
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let r = rows.filter((x) => words.every((w) => x.text.includes(w)) && (!movers || x.pm > 100));
    const mag = (x: Row) => x.src.mag ?? 99;
    const cmp: Record<Sort, (a: Row, b: Row) => number> = {
      notable: (a, b) => Number(b.src.refs ?? 0) - Number(a.src.refs ?? 0) || Number(b.cat === "simbad") - Number(a.cat === "simbad") || mag(a) - mag(b),
      near: (a, b) => (a.sep ?? 0) - (b.sep ?? 0),
      bright: (a, b) => mag(a) - mag(b),
      fast: (a, b) => b.pm - a.pm,
    };
    r = [...r].sort(cmp[sort]);
    return r;
  }, [rows, q, sort, movers]);

  return (
    <div className="objects" onPointerLeave={() => p.onHover(null)}>
      <div className="micro menu-title">Objects in view</div>
      <div className="obj-cats">
        {CATALOG_IDS.map((c) => {
          const st = p.status[c];
          const active = p.overlays.includes(c);
          const count = active ? rows.filter((r) => r.cat === c).length : null;
          return (
            <button key={c} className={`chip obj-cat${active ? " on" : ""}`} onClick={() => p.onToggle(c)} aria-pressed={active} title={st?.state === "error" ? st.message : `${active ? "Hide" : "Show"} ${CATALOG_STYLE[c].label}`}>
              <span className={`swatch ${CATALOG_STYLE[c].shape}`} style={{ color: CATALOG_STYLE[c].color }} />
              {CATALOG_STYLE[c].label}
              {active && <span className="obj-count">{st?.state === "loading" ? "…" : st?.state === "error" ? "!" : count}</span>}
            </button>
          );
        })}
      </div>
      {!on.length ? (
        <p className="form-hint">Turn on a catalogue to list its objects. SIMBAD has names and object types; Gaia has motions and distances.</p>
      ) : (
        <>
          <label className="obj-search">
            <Search size={13} aria-hidden />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter: name, type (e.g. “variable”, “M4”)" aria-label="Filter objects" spellCheck={false} />
          </label>
          <div className="obj-sort">
            <Segmented<Sort>
              label="Sort objects"
              size="sm"
              value={sort}
              onChange={setSort}
              options={[
                { value: "notable", label: "Notable", title: "Most studied first (SIMBAD references), then brightest" },
                { value: "near", label: "Nearest", title: "Closest to the target first" },
                { value: "bright", label: "Brightest" },
                { value: "fast", label: "Fastest", title: "Largest proper motion first" },
              ]}
            />
            <button className={`chip${movers ? " on" : ""}`} onClick={() => setMovers((m) => !m)} aria-pressed={movers} title="Only sources moving faster than 100 mas/yr">
              movers
            </button>
          </div>
          <div className="obj-list" role="list">
            {shown.slice(0, ROWS).map((r) => (
              <button key={r.key} role="listitem" className="obj-row" onPointerEnter={() => p.onHover(r.key)} onFocus={() => p.onHover(r.key)} onClick={() => p.onPick(r.cat, r.src)}>
                <span className={`swatch ${CATALOG_STYLE[r.cat].shape}`} style={{ color: CATALOG_STYLE[r.cat].color }} />
                <span className="obj-main">
                  <span className={`obj-name${r.cat === "simbad" ? "" : " mono"}`}>{String(r.src.id)}</span>
                  <span className="obj-sub">
                    {r.cat === "simbad" ? [r.src.type, r.src.sp_type].filter(Boolean).join(" · ") || "SIMBAD" : CATALOG_STYLE[r.cat].label}
                  </span>
                </span>
                <span className="obj-nums mono">
                  <span>{r.src.mag != null ? `${r.src.mag_band} ${r.src.mag.toFixed(1)}` : "—"}</span>
                  <span className="muted">{r.pm >= 100 ? <b className="obj-fast">{(r.pm / 1000).toFixed(2)}″/yr</b> : r.sep != null ? formatAngle(r.sep) : ""}</span>
                </span>
              </button>
            ))}
            {!shown.length && <p className="form-hint">{rows.length ? "Nothing matches the filter." : on.some((c) => p.status[c]?.state === "loading") ? "Loading catalogues…" : "No sources in this field."}</p>}
          </div>
          {shown.length > ROWS && <p className="form-hint">Showing {ROWS} of {shown.length}; filter to narrow down.</p>}
        </>
      )}
    </div>
  );
}
