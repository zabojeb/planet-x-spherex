/** Small design-system primitives: popover, segmented control, tabs, switch, icon button. */
import { useCallback, useRef, useState, type ReactNode } from "react";
import { useDismiss } from "../../hooks";

export function Popover(props: {
  label: ReactNode;
  title: string;
  align?: "left" | "right";
  width?: number;
  className?: string;
  children: ReactNode | ((close: () => void) => ReactNode);
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  return (
    <div className={`popover-wrap ${props.className ?? ""}`} ref={ref}>
      <button
        className={`btn ghost${open ? " active" : ""}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={props.title}
        onClick={() => setOpen((o) => !o)}
      >
        {props.label}
      </button>
      {open && (
        <div className={`popover ${props.align === "left" ? "left" : "right"}`} role="dialog" aria-label={props.title} style={{ width: props.width }}>
          {typeof props.children === "function" ? props.children(close) : props.children}
        </div>
      )}
    </div>
  );
}

export interface SegOption<T extends string | number> {
  value: T;
  label: ReactNode;
  title?: string;
  disabled?: boolean;
}

export function Segmented<T extends string | number>(props: {
  value: T;
  options: SegOption<T>[];
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div className={`segmented ${props.size ?? "md"} ${props.className ?? ""}`} role="radiogroup" aria-label={props.label}>
      {props.options.map((o) => (
        <button
          key={String(o.value)}
          role="radio"
          aria-checked={props.value === o.value}
          className={props.value === o.value ? "on" : ""}
          disabled={o.disabled}
          title={o.title}
          onClick={() => props.onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>(props: { value: T; tabs: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {props.tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={props.value === t.value} className={props.value === t.value ? "on" : ""} onClick={() => props.onChange(t.value)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Switch(props: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; hint?: ReactNode; disabled?: boolean }) {
  return (
    <label className={`switch-row${props.disabled ? " disabled" : ""}`}>
      <span className="switch-text">
        {props.label}
        {props.hint && <span className="switch-hint">{props.hint}</span>}
      </span>
      <input type="checkbox" role="switch" checked={props.checked} disabled={props.disabled} onChange={(e) => props.onChange(e.target.checked)} />
      <span className="switch" aria-hidden />
    </label>
  );
}

export function MicroLabel({ children }: { children: ReactNode }) {
  return <div className="micro">{children}</div>;
}
