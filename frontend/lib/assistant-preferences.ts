/**
 * Per-browser Ask Pivot preferences — today, only whether the debug panels are shown.
 *
 * `localStorage` (it should outlive the tab, unlike the conversation), read through
 * `useSyncExternalStore` so the server render and the hydration render agree (debug off)
 * and the stored choice applies straight after. Every access is guarded: storage throws
 * rather than returning null in Safari's private mode, and a preference that cannot be
 * saved should be lost quietly, not take the page down. Same idiom as the sidebar's
 * collapsed state in `components/shell.tsx`.
 */

import { useSyncExternalStore } from "react";

export const DEBUG_KEY = "pivot.assistant.debug";
const listeners = new Set<() => void>();
/** In-memory fallback, so the toggle still works for this page when storage is blocked. */
let memory: boolean | null = null;

function readDebug(): boolean {
  if (memory !== null) return memory;
  try {
    return window.localStorage.getItem(DEBUG_KEY) === "on";
  } catch {
    return false;
  }
}

export function setDebugPreference(on: boolean): void {
  memory = on;
  try {
    window.localStorage.setItem(DEBUG_KEY, on ? "on" : "off");
  } catch {
    /* private mode: the choice lasts for this page only */
  }
  listeners.forEach((l) => l());
}

export function useDebugPreference(): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    readDebug,
    () => false,
  );
  return [on, setDebugPreference];
}

/** Tests only. */
export function resetPreferencesForTests(): void {
  memory = null;
}
