/** The sky position under the mouse, shared from SkyView to the coordinate box without re-rendering the app on
 *  every mouse move: a tiny external store read with useSyncExternalStore. */
import { useSyncExternalStore } from "react";

export interface CursorReadout {
  /** α, δ in degrees (ICRS) */
  pos: [number, number];
  /** pixel value under the cursor, formatted for the current display, if any */
  value: string | null;
  /** offset from the target, e.g. "12.3″ NE of target", if there is a target */
  offset: string | null;
}

let current: CursorReadout | null = null;
const listeners = new Set<() => void>();

export function setCursorReadout(next: CursorReadout | null): void {
  const same =
    next === current ||
    (next && current && next.pos[0] === current.pos[0] && next.pos[1] === current.pos[1] && next.value === current.value && next.offset === current.offset);
  if (same) return;
  current = next;
  listeners.forEach((f) => f());
}

const subscribe = (f: () => void) => {
  listeners.add(f);
  return () => listeners.delete(f);
};

export const useCursorReadout = () => useSyncExternalStore(subscribe, () => current);
