/**
 * Search box with suggestions as you type (⌘K / Ctrl+K). Suggestions are computed locally (src/search.ts)
 * on every keystroke; nothing is built or downloaded until a suggestion is picked. Names the local index
 * does not know are looked up after a short pause, only to show what they are.
 */
import { CornerDownLeft, Crosshair, Dices, History, Loader2, Orbit, Search, SearchX, X } from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, lookupName } from "../api";
import { formatDec, formatRA } from "../astro";
import { useDismiss, useMediaQuery } from "../hooks";
import {
  describe, facetObjects, looksLikeCoords, matchFacet, nearby, normalize, parseCoords, pickCoords, pickFor, rankObjects,
  smallBodyName, solarSystemQuery, SOLAR_EXAMPLES, type Facet, type Pick, type Recent, type SkyObject,
} from "../search";
import type { Target } from "../types";
import { ClaudeEgg } from "./ClaudeEgg";

interface Props {
  space: SkyObject[];
  indexReady: boolean;
  /** "p:<preset id>" or "n:<name>" of what is on screen, to mark it in the list. */
  current: string | null;
  recents: Recent[];
  onPick: (pick: Pick, recent: Recent) => void;
  onRandom: (what: Facet | "field" | null) => void;
  /** Start loading the object index now (first focus). */
  onWarm?: () => void;
  /** Look up a comet or asteroid and open a co-moving sequence (IRSA MOST). */
  onMoving?: (name: string) => void;
}

type Remote = { query: string; state: "loading" | "found" | "missing" | "error"; target?: Target; message?: string };

type Item =
  | { kind: "object"; obj: SkyObject; via: string | null; text: string; fuzzy?: boolean; dist?: number }
  | { kind: "coords"; ra: number; dec: number }
  | { kind: "random"; what: Facet | "field" | null; label: string; sub: string }
  | { kind: "recent"; recent: Recent }
  | { kind: "remote"; remote: Remote }
  | { kind: "moving"; name: string; label: string; sub: string };

type Section = { title: string; items: Item[] };

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
const REMOTE_DELAY_MS = 350;
/** The words that summon the easter egg instead of a search (and are never sent to the name resolver). */
const EGG = new Set(["claude", "клод"]);

const keyOf = (o: SkyObject) => (o.preset ? `p:${o.preset}` : `n:${o.name}`);
const pickKey = (p: Pick) => (p.kind === "preset" ? `p:${p.id}` : `n:${p.target.name ?? ""}`);
const selectable = (it: Item) => !(it.kind === "remote" && it.remote.state !== "found" && it.remote.state !== "error");

/** Bold the parts of `text` that start with a query word, so it is visible why a row matched. */
function Highlight({ text, query }: { text: string; query: string }) {
  const words = normalize(query).split(" ").filter(Boolean);
  if (!words.length) return <>{text}</>;
  const marks: boolean[] = new Array(text.length).fill(false);
  const lower = text.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");
  if (lower.length === text.length) {
    for (const w of words) {
      const re = new RegExp(`(^|[^a-z0-9])(${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "g");
      for (let m = re.exec(lower); m; m = re.exec(lower)) {
        const start = m.index + m[1].length;
        for (let i = start; i < start + m[2].length; i++) marks[i] = true;
        if (re.lastIndex === m.index) re.lastIndex++;
      }
    }
  }
  const out: ReactNode[] = [];
  let i = 0;
  while (i < text.length) {
    let j = i;
    while (j < text.length && marks[j] === marks[i]) j++;
    out.push(marks[i] ? <mark key={i}>{text.slice(i, j)}</mark> : text.slice(i, j));
    i = j;
  }
  return <>{out}</>;
}

function recentFor(it: Item, query: string): Recent | null {
  if (it.kind === "object") return { label: it.obj.name, sub: describe(it.obj), pick: pickFor(it.obj) };
  if (it.kind === "coords") {
    const pick = pickCoords(it.ra, it.dec);
    return { label: pick.kind === "target" ? pick.target.name ?? "" : "", sub: "Position", pick };
  }
  if (it.kind === "recent") return it.recent;
  if (it.kind === "remote" && it.remote.target) {
    const t = it.remote.target;
    const pick: Pick = t.id ? { kind: "preset", id: t.id } : { kind: "target", target: { ...t, name: t.name || query } };
    return { label: t.name || query, sub: [t.description, t.source].filter(Boolean).join(" · "), pick };
  }
  return null;
}

export function CommandSearch(p: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const narrow = useMediaQuery("(max-width: 760px)");
  const [remote, setRemote] = useState<Remote | null>(null);
  const [waiting, setWaiting] = useState(false); // Enter pressed on a name still being looked up
  const [egg, setEgg] = useState(false);
  const closeEgg = useCallback(() => setEgg(false), []);
  const wrap = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(wrap, open, close);

  const focus = () => {
    setOpen(true);
    p.onWarm?.();
    requestAnimationFrame(() => input.current?.focus());
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        focus();
        input.current?.select();
      } else if (e.key === "/" && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const query = text.trim();
  const coords = useMemo(() => parseCoords(query), [query]);
  const facet = useMemo(() => (coords ? null : matchFacet(query)), [coords, query]);
  const matches = useMemo(() => (query && !coords ? rankObjects(p.space, query, 7) : []), [p.space, query, coords]);
  const exact = matches.length > 0 && matches[0].score - matches[0].obj.pop >= 100;
  const solar = useMemo(() => (p.onMoving && !coords ? solarSystemQuery(query) : null), [p.onMoving, coords, query]);

  // Names the index does not know: ask the resolver after a pause in typing (only to show what they are).
  // Not while good local suggestions exist, except for catalogue numbers (HD 12345 may not be in the index
  // even when HD 123… are).
  const weak = !matches.length || matches[0].score - matches[0].obj.pop < 60;
  const isEgg = EGG.has(query.toLowerCase());
  const wantRemote = query.length >= 2 && !isEgg && !coords && !exact && !facet && solar !== "strong" && !looksLikeCoords(query) && (weak || /\d/.test(query));
  useEffect(() => {
    if (!wantRemote) {
      setRemote(null);
      return;
    }
    let live = true;
    setRemote((r) => (r?.query === query ? r : { query, state: "loading" }));
    const t = window.setTimeout(() => {
      lookupName(query).then(
        (target) => live && setRemote({ query, state: "found", target }),
        (e) =>
          live &&
          setRemote(
            e instanceof ApiError && e.status === 404 && /^Unknown object/.test(e.message)
              ? { query, state: "missing" }
              : { query, state: "error", message: e instanceof Error ? e.message : String(e) },
          ),
      );
    }, waiting ? 0 : REMOTE_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [query, wantRemote, waiting]);
  // The resolver has not answered this exact text yet (or has not been asked: typing outran the effect).
  const pending = wantRemote && !(remote?.query === query && remote.state !== "loading");

  const sections: Section[] = useMemo(() => {
    const out: Section[] = [];
    if (!query) {
      const recents = p.recents.slice(0, 4);
      if (recents.length) out.push({ title: "Recent", items: recents.map((recent) => ({ kind: "recent", recent })) });
      const presets = p.space.filter((o) => o.cat === "challenge");
      if (presets.length) out.push({ title: "Challenge targets", items: presets.map((obj) => ({ kind: "object", obj, via: null, text: obj.name })) });
      out.push({
        title: "Explore",
        items: [
          { kind: "random", what: null, label: "Random object", sub: "A brown dwarf, a fast star, a variable, a galaxy, a nebula…" },
          { kind: "random", what: "field", label: "Random patch of sky", sub: "Anywhere: SPHEREx covers the whole sky every six months" },
        ],
      });
      return out;
    }
    if (solar === "term") {
      out.push({
        title: "Solar system · co-moving",
        items: SOLAR_EXAMPLES.map((e): Item => ({ kind: "moving", name: e.name, label: e.label, sub: `${e.sub} · type any comet or asteroid name` })),
      });
      return out;
    }
    if (coords) {
      out.push({ title: "Position", items: [{ kind: "coords", ra: coords.ra, dec: coords.dec }] });
      const near = nearby(p.space, coords.ra, coords.dec);
      if (near.length) out.push({ title: "Known objects nearby", items: near.map(({ obj, dist }) => ({ kind: "object", obj, via: null, text: obj.name, dist })) });
      return out;
    }
    const movingItem: Item | null = p.onMoving && query.length >= 2 && /[a-z]/i.test(query) && !looksLikeCoords(query)
      ? { kind: "moving", name: smallBodyName(query), label: `Track “${smallBodyName(query)}” as a solar-system object`, sub: "Comet or asteroid: images follow it, stars trail (IRSA MOST)" }
      : null;
    if (movingItem && solar === "strong" && !exact) out.push({ title: "Solar system", items: [movingItem] });
    // A name the resolver knows beats near misses: what was typed exists, so it goes first.
    const literal = remote && remote.query === query && remote.state === "found" && !exact && (/\d/.test(query) || matches[0]?.fuzzy);
    if (literal) out.push({ title: "Name lookup", items: [{ kind: "remote", remote }] });
    if (matches.length) out.push({ title: matches[0].fuzzy ? "Did you mean" : "Objects", items: matches.map((m) => ({ kind: "object", ...m })) });
    if (facet) {
      const shown = new Set(matches.map((m) => m.obj));
      const top = facetObjects(p.space, facet, 5).filter((o) => !shown.has(o)).slice(0, matches.length ? 3 : 5);
      const count = p.space.filter(facet.test).length;
      out.push({
        title: `${facet.label} · ${count}`,
        items: [
          { kind: "random", what: facet, label: `Random ${facet.one}`, sub: `One of ${count} in the index, picked at random` },
          ...top.map((obj): Item => ({ kind: "object", obj, via: null, text: obj.name })),
        ],
      });
    }
    if (remote && remote.query === query && !literal && !(remote.state === "missing" && matches.length))
      out.push({ title: matches.length ? "Elsewhere" : "Name lookup", items: [{ kind: "remote", remote }] });
    if (movingItem && !(solar === "strong" && !exact)) out.push({ title: "Solar system", items: [movingItem] });
    return out;
  }, [query, coords, matches, facet, remote, p.space, p.recents, solar, exact, p.onMoving]);

  const items = useMemo(() => sections.flatMap((s) => s.items), [sections]);
  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    if (active >= items.length) setActive(Math.max(0, items.length - 1));
  }, [items.length, active]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const finish = () => {
    setText("");
    setOpen(false);
    setWaiting(false);
    input.current?.blur();
  };

  const choose = (it: Item | undefined) => {
    if (!it) return;
    if (it.kind === "random") {
      p.onRandom(it.what);
      finish();
      return;
    }
    if (it.kind === "moving") {
      p.onMoving?.(it.name);
      finish();
      return;
    }
    if (it.kind === "remote") {
      if (it.remote.state === "error") {
        // Retry the lookup.
        setRemote({ query, state: "loading" });
        lookupName(query).then(
          (target) => setRemote({ query, state: "found", target }),
          (e) => setRemote({ query, state: e instanceof ApiError && e.status === 404 ? "missing" : "error", message: String(e?.message ?? e) }),
        );
        return;
      }
      if (it.remote.state === "loading") {
        setWaiting(true);
        return;
      }
    }
    const recent = recentFor(it, query);
    if (!recent) return;
    p.onPick(recent.pick, recent);
    finish();
  };

  // Enter on a name still being looked up: open it as soon as the answer arrives.
  useEffect(() => {
    if (!waiting || !remote || remote.query !== query) return;
    if (remote.state === "found") choose({ kind: "remote", remote });
    else if (remote.state !== "loading") setWaiting(false);
  }, [waiting, remote]); // eslint-disable-line react-hooks/exhaustive-deps

  const move = (dir: 1 | -1) => {
    if (!items.length) return;
    let i = active;
    for (let n = 0; n < items.length; n++) {
      i = (i + dir + items.length) % items.length;
      if (selectable(items[i])) break;
    }
    setActive(i);
  };

  // Inline completion: the rest of the top suggestion's name, shown in grey after the caret.
  const top = items[active];
  const ghost =
    active === 0 && top?.kind === "object" && text && top.text.toLowerCase().startsWith(text.toLowerCase()) && top.text.length > text.length
      ? top.text.slice(text.length)
      : "";

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      move(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      move(-1);
    } else if ((e.key === "Tab" || (e.key === "ArrowRight" && e.currentTarget.selectionStart === text.length)) && ghost && !e.shiftKey) {
      e.preventDefault();
      setText(text + ghost);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (isEgg) {
        finish();
        setEgg(true);
        return;
      }
      // A catalogue number typed in full and still being looked up: Enter means that object, not the
      // first near miss, so wait for the answer.
      if (pending && active === 0 && (!matches.length || /\d/.test(query))) setWaiting(true);
      else choose(items[active]);
    } else if (e.key === "Escape") {
      if (text) setText("");
      else {
        setOpen(false);
        input.current?.blur();
      }
    }
  };

  const hint = (() => {
    if (!query) return <>Names, catalogue numbers (HD, HIP, GJ, NGC, M, 2MASS, WISE), object types, comets and asteroids, or RA Dec</>;
    if (coords) return <>RA {formatRA(coords.ra)} · Dec {formatDec(coords.dec)} (J2000) · <kbd className="kbd">↵</kbd> opens it</>;
    if (looksLikeCoords(query)) return <>RA Dec as <span className="mono">269.452 4.693</span>, <span className="mono">17:57:48.5 +04:41:36</span> or <span className="mono">17h57m48s +4d41m36s</span></>;
    if (remote?.state === "loading" && (!matches.length || waiting)) return <>Not in the local index of {p.space.length.toLocaleString("en")} objects: asking SIMBAD, NED and VizieR…</>;
    if (remote?.state === "missing" && remote.query === query && matches.length) return <>SIMBAD, NED and VizieR know no “{query}” · <kbd className="kbd">↑</kbd><kbd className="kbd">↓</kbd> pick a suggestion</>;
    return (
      <>
        <kbd className="kbd">↑</kbd><kbd className="kbd">↓</kbd> choose · {ghost ? <><kbd className="kbd">Tab</kbd> complete · </> : null}<kbd className="kbd">↵</kbd> open · <kbd className="kbd">Esc</kbd> {text ? "clear" : "close"}
      </>
    );
  })();

  let n = -1;
  return (
    <div className={`cmdk${open ? " open" : ""}`} ref={wrap}>
      <div className="cmdk-input" onClick={focus}>
        <Search size={16} className="cmdk-icon" aria-hidden />
        <div className="cmdk-field">
          <input
            ref={input}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setOpen(true);
              setWaiting(false);
            }}
            onFocus={() => {
              setOpen(true);
              p.onWarm?.();
            }}
            onKeyDown={onKeyDown}
            placeholder={narrow ? "Object or RA Dec" : "Search a star, galaxy, nebula or RA Dec"}
            aria-label="Search an object or coordinates"
            aria-expanded={open}
            aria-controls="cmdk-list"
            aria-activedescendant={open && items.length ? `cmdk-opt-${active}` : undefined}
            aria-autocomplete="list"
            role="combobox"
            spellCheck={false}
            autoComplete="off"
          />
          {ghost && (
            <span className="cmdk-ghost" aria-hidden>
              <span className="cmdk-ghost-typed">{text}</span>
              {ghost}
            </span>
          )}
        </div>
        {waiting || (remote?.state === "loading" && remote.query === query && !matches.length) ? (
          <Loader2 size={16} className="spin cmdk-icon" aria-label="Looking up" />
        ) : text ? (
          <button className="cmdk-clear" aria-label="Clear" onClick={(e) => { e.stopPropagation(); setText(""); input.current?.focus(); }}>
            <X size={14} />
          </button>
        ) : (
          <kbd className="kbd cmdk-shortcut">{isMac ? "⌘" : "Ctrl"} K</kbd>
        )}
      </div>
      {open && (
        <div className="cmdk-list" id="cmdk-list" role="listbox" ref={list}>
          {sections.map((s) => (
            <Fragment key={s.title}>
              <div className="micro cmdk-section">{s.title}</div>
              {s.items.map((it) => {
                n += 1;
                const i = n;
                return (
                  <Row
                    key={i}
                    i={i}
                    item={it}
                    query={query}
                    active={i === active}
                    current={p.current}
                    waiting={waiting}
                    onHover={() => selectable(it) && setActive(i)}
                    onChoose={() => choose(it)}
                  />
                );
              })}
            </Fragment>
          ))}
          {query && !items.length && (
            <div className="cmdk-empty">
              {p.indexReady ? <>Nothing matches “{query}”. Try a common name, a catalogue number or RA Dec.</> : <>Loading the object index…</>}
            </div>
          )}
          <div className="cmdk-hint">{hint}</div>
        </div>
      )}
      {egg && <ClaudeEgg onDone={closeEgg} />}
    </div>
  );
}

function Row(p: { i: number; item: Item; query: string; active: boolean; current: string | null; waiting: boolean; onHover: () => void; onChoose: () => void }) {
  const it = p.item;
  let icon: ReactNode = null;
  let name: ReactNode;
  let tag: ReactNode = null;
  let desc: ReactNode;
  let disabled = false;
  if (it.kind === "object") {
    const o = it.obj;
    name = <Highlight text={o.name} query={p.query} />;
    if (p.current === keyOf(o)) tag = <span className="cmdk-current">viewing</span>;
    else if (o.preset) tag = <span className="cmdk-tag accent">challenge</span>;
    const extra = it.dist != null ? `${(it.dist * 60).toFixed(it.dist * 60 < 10 ? 1 : 0)}′ away` : it.via ? <>also <Highlight text={it.via} query={p.query} /></> : null;
    desc = (
      <>
        {describe(o)}
        {extra && <> · {extra}</>}
      </>
    );
  } else if (it.kind === "coords") {
    icon = <Crosshair size={14} />;
    name = (
      <span className="mono">
        {formatRA(it.ra)} {formatDec(it.dec)}
      </span>
    );
    desc = `Go to this position · ${it.ra.toFixed(5)}°, ${it.dec >= 0 ? "+" : ""}${it.dec.toFixed(5)}°`;
  } else if (it.kind === "random") {
    icon = <Dices size={14} />;
    name = it.label;
    desc = it.sub;
  } else if (it.kind === "moving") {
    icon = <Orbit size={14} />;
    name = it.label;
    tag = <span className="cmdk-tag">co-moving</span>;
    desc = it.sub;
  } else if (it.kind === "recent") {
    icon = <History size={14} />;
    name = it.recent.label;
    if (p.current === pickKey(it.recent.pick)) tag = <span className="cmdk-current">viewing</span>;
    desc = it.recent.sub;
  } else {
    const r = it.remote;
    if (r.state === "loading") {
      icon = <Loader2 size={14} className="spin" />;
      name = <>Looking up “{r.query}”…</>;
      desc = p.waiting ? "Opens as soon as the name is found" : "SIMBAD, NED and VizieR through CDS Sesame";
      disabled = !p.waiting;
    } else if (r.state === "found" && r.target) {
      name = r.target.name || r.query;
      tag = <span className="cmdk-tag">Sesame</span>;
      desc = [r.target.description, `${formatRA(r.target.ra)} ${formatDec(r.target.dec)}`].filter(Boolean).join(" · ");
    } else if (r.state === "missing") {
      icon = <SearchX size={14} />;
      name = <>No object called “{r.query}”</>;
      desc = "Not in SIMBAD, NED or VizieR. Check the spelling, or enter RA Dec.";
      disabled = true;
    } else {
      icon = <SearchX size={14} />;
      name = "Name lookup is unavailable right now";
      desc = `${r.message ?? "The resolver did not answer"} · Enter to retry`;
    }
  }
  return (
    <button
      id={`cmdk-opt-${p.i}`}
      data-i={p.i}
      role="option"
      aria-selected={p.active}
      aria-disabled={disabled || undefined}
      className={`cmdk-item${p.active && !disabled ? " active" : ""}${disabled ? " disabled" : ""}`}
      onMouseEnter={p.onHover}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => !disabled && p.onChoose()}
      tabIndex={-1}
    >
      <span className="cmdk-name">
        {icon && <span className="cmdk-row-icon">{icon}</span>}
        <span className="cmdk-label">{name}</span>
        {tag}
      </span>
      <span className="cmdk-desc">{desc}</span>
      {p.active && !disabled && <CornerDownLeft size={14} className="cmdk-enter" aria-hidden />}
    </button>
  );
}
