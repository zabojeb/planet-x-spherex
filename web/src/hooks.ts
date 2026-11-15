import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** Width of an element, kept up to date with a ResizeObserver. */
export function useElementWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

export function useViewportHeight(): number {
  const [vh, setVh] = useState(window.innerHeight);
  useEffect(() => {
    const on = () => setVh(window.innerHeight);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return vh;
}

/** Keyboard Lock API (Chromium only; not in lib.dom yet). */
type KeyboardLock = { lock?: (codes?: string[]) => Promise<void>; unlock?: () => void };
const keyboardLock = (): KeyboardLock | undefined => (navigator as Navigator & { keyboard?: KeyboardLock }).keyboard;

/** The page's own fullscreen, with Escape kept for the app: Chromium's Keyboard Lock hands Esc to the page,
 *  so it closes the card or drawer instead of leaving fullscreen (press and hold Esc, F or the button leaves).
 *  Other browsers lack the lock and still leave on Esc; the browser's own fullscreen (⌃⌘F, F11) can't be locked. */
export function useFullscreen(): { supported: boolean; on: boolean; toggle: () => void } {
  const supported = typeof document !== "undefined" && !!document.fullscreenEnabled;
  const [on, setOn] = useState(() => supported && !!document.fullscreenElement);
  useEffect(() => {
    if (!supported) return;
    const sync = () => {
      const now = !!document.fullscreenElement;
      setOn(now);
      if (!now) keyboardLock()?.unlock?.();
    };
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, [supported]);
  const toggle = () => {
    if (!supported) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => {});
      return;
    }
    document.documentElement
      .requestFullscreen({ navigationUI: "hide" })
      .then(() => keyboardLock()?.lock?.(["Escape"]))
      .catch(() => {}); // refused (no user gesture, iframe without allow="fullscreen") or no lock: plain fullscreen
  };
  return { supported, on, toggle };
}

/** Calls onDismiss on a pointer-down outside `ref` or on Escape, while `active`. */
export function useDismiss(ref: React.RefObject<HTMLElement>, active: boolean, onDismiss: () => void): void {
  useEffect(() => {
    if (!active) return;
    const down = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onDismiss();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // the app's own Escape (close the card, then the drawer) waits for the next press
      onDismiss();
    };
    document.addEventListener("pointerdown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", down);
      document.removeEventListener("keydown", key);
    };
  }, [ref, active, onDismiss]);
}

export function safeStorage(key: string, value?: string): string | null {
  try {
    if (value !== undefined) window.localStorage.setItem(key, value);
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
