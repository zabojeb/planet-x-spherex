/** Command-palette search: object names or coordinates, with the challenge targets as quick picks (⌘K / Ctrl+K). */
import { CornerDownLeft, Loader2, Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDismiss, useMediaQuery } from "../hooks";
import type { Target } from "../types";

interface Props {
  presets: Target[];
  activeId: string | null;
  busy: boolean;
  error: string | null;
  onSearch: (q: string) => void;
  onPreset: (id: string) => void;
  compact?: boolean;
}

type Item = { kind: "preset"; target: Target } | { kind: "search"; text: string };

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

export function CommandSearch(p: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const narrow = useMediaQuery("(max-width: 760px)");
  // the shortcut hint only helps with a hardware keyboard and a mouse/trackpad
  const hasKeyboard = useMediaQuery("(hover: hover) and (pointer: fine)") && !narrow;
  const wrap = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(wrap, open, close);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
        requestAnimationFrame(() => input.current?.focus());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const items: Item[] = useMemo(() => {
    const q = text.trim().toLowerCase();
    const presets = p.presets.filter(
      (t) => !q || [t.name, t.id, t.description].some((s) => (s ?? "").toLowerCase().includes(q)),
    );
    const out: Item[] = presets.map((target) => ({ kind: "preset", target }));
    if (q) out.push({ kind: "search", text: text.trim() });
    return out;
  }, [p.presets, text]);

  useEffect(() => setActive(0), [text]);

  const choose = (it: Item | undefined) => {
    if (!it) return;
    if (it.kind === "preset" && it.target.id) p.onPreset(it.target.id);
    else if (it.kind === "search") p.onSearch(it.text);
    if (it.kind === "preset") {
      setText("");
      setOpen(false);
      input.current?.blur();
    }
  };

  // Close after a successful search; keep open to show an error.
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !p.busy && !p.error) {
      setOpen(false);
      setText("");
      input.current?.blur();
    }
    wasBusy.current = p.busy;
  }, [p.busy, p.error]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(items.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(items[active] ?? (text.trim() ? { kind: "search", text: text.trim() } : undefined));
    } else if (e.key === "Escape") {
      setOpen(false);
      input.current?.blur();
    }
  };

  return (
    <div className={`cmdk${open ? " open" : ""}${p.compact ? " compact" : ""}`} ref={wrap}>
      {p.compact && !open ? (
        <button className="btn ghost icon" aria-label="Search an object or coordinates" onClick={() => { setOpen(true); requestAnimationFrame(() => input.current?.focus()); }}>
          <Search size={16} />
        </button>
      ) : (
        <div className="cmdk-input">
          <Search size={16} className="cmdk-icon" aria-hidden />
          <input
            ref={input}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
            placeholder={narrow ? "Object or RA Dec" : "Search an object or coordinates"}
            aria-label="Search an object or coordinates"
            aria-expanded={open}
            aria-controls="cmdk-list"
            role="combobox"
            spellCheck={false}
            autoComplete="off"
          />
          {p.busy ? (
            <Loader2 size={16} className="spin cmdk-icon" aria-label="Searching" />
          ) : text ? (
            <button className="cmdk-clear" aria-label="Clear" onClick={() => setText("")}>
              <X size={14} />
            </button>
          ) : (
            !p.compact && hasKeyboard && <kbd className="kbd">{isMac ? "⌘" : "Ctrl"} K</kbd>
          )}
        </div>
      )}
      {open && (
        <div className="cmdk-list" id="cmdk-list" role="listbox">
          {p.error && <div className="cmdk-error" role="alert">{p.error}</div>}
          {!text && <div className="micro cmdk-section">Challenge targets</div>}
          {items.map((it, i) => (
            <button
              key={it.kind === "preset" ? it.target.id ?? i : "search"}
              role="option"
              aria-selected={i === active}
              className={`cmdk-item${i === active ? " active" : ""}`}
              onMouseEnter={() => setActive(i)}
              onClick={() => choose(it)}
            >
              {it.kind === "preset" ? (
                <>
                  <span className="cmdk-name">
                    {it.target.name}
                    {p.activeId === it.target.id && <span className="cmdk-current">viewing</span>}
                  </span>
                  <span className="cmdk-desc">{it.target.description}</span>
                </>
              ) : (
                <>
                  <span className="cmdk-name">
                    Search “{it.text}”
                  </span>
                  <span className="cmdk-desc">Object name (SIMBAD/NED via CDS Sesame) or RA Dec in degrees or sexagesimal</span>
                </>
              )}
              {i === active && <CornerDownLeft size={14} className="cmdk-enter" aria-hidden />}
            </button>
          ))}
          {!items.length && <div className="cmdk-empty">Type a name like “Vega” or coordinates like “08:55:10.8 −07:14:42”.</div>}
        </div>
      )}
    </div>
  );
}
