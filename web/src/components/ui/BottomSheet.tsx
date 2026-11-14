import { useState, type ReactNode } from "react";

/** Mobile controls: a persistent summary row with a handle that expands the full controls. */
export function BottomSheet({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`sheet${open ? " open" : ""}`}>
      <button className="sheet-handle" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={open ? "Collapse controls" : "More controls"}>
        <span />
      </button>
      <div className="sheet-summary">{summary}</div>
      {open && <div className="sheet-body">{children}</div>}
    </div>
  );
}
