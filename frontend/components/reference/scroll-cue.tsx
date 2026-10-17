"use client";

/**
 * A right-edge cue for content that scrolls sideways.
 *
 * At phone widths a wide table or a tab strip is cut at the panel edge, often mid-word, and
 * nothing said there was more. The cue appears only while the content actually overflows
 * and is not already scrolled to its end, so a table that fits never carries it.
 *
 * Two modes:
 * - default: this component IS the scroll container (`overflow-x-auto`) around `children`;
 * - `target`: the scroll container already exists inside `children` (e.g. the shared
 *   `Tabs` strip, `[role="tablist"]`) and is observed in place, unchanged.
 *
 * Purely visual: the cue is `aria-hidden` and ignores the pointer, so it never covers a
 * tab or a cell for anyone using them.
 */

import clsx from "clsx";
import { useEffect, useRef, useState } from "react";

/** The cue is painted in the surface it sits on, so the fade reads as the content ending. */
const SURFACE = {
  panel: { fade: "from-panel", tab: "bg-panel" },
  ink: { fade: "from-ink", tab: "bg-ink" },
} as const;

type Cue = { more: boolean; right: number; top: number; height: number };
const HIDDEN: Cue = { more: false, right: 0, top: 0, height: 0 };

export function ScrollCue({
  children,
  target,
  label = "Scroll",
  surface = "panel",
  className,
  scrollClassName,
  cueClassName,
}: {
  children: React.ReactNode;
  /** Selector for an existing scroll container inside `children`. */
  target?: string;
  /** Word printed before the arrow; `null` prints the arrow alone (tab strips). */
  label?: string | null;
  /** The background behind the scroller. */
  surface?: keyof typeof SURFACE;
  className?: string;
  /** Classes for the scroll container this component creates (default mode only). */
  scrollClassName?: string;
  /** Extra classes for the cue itself. */
  cueClassName?: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Where the scroller sits inside the host, so the cue lines up with its real edge (a tab
  // strip that bleeds into its padding with `-mx-1` ends past the host's own edge).
  const [cue, setCue] = useState<Cue>(HIDDEN);

  useEffect(() => {
    const host = hostRef.current;
    const el = target ? host?.querySelector<HTMLElement>(target) : scrollRef.current;
    if (!host || !el) return;
    const update = () => {
      const h = host.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      const next: Cue = {
        more: el.scrollWidth - el.clientWidth - el.scrollLeft > 2,
        right: Math.round(h.right - r.right),
        top: Math.round(r.top - h.top),
        height: el.clientHeight,
      };
      setCue((prev) =>
        prev.more === next.more &&
        prev.right === next.right &&
        prev.top === next.top &&
        prev.height === next.height
          ? prev
          : next,
      );
    };
    // ResizeObserver reports every observed element once on `observe`, which sets the
    // initial state; it then catches the container or its content changing width (a tab
    // switch swapping the table, a font finishing loading, a <details> opening).
    const observer = new ResizeObserver(update);
    observer.observe(host);
    observer.observe(el);
    for (const child of Array.from(el.children)) observer.observe(child);
    el.addEventListener("scroll", update, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", update);
    };
  }, [target]);

  return (
    <div ref={hostRef} className={clsx("relative min-w-0", className)}>
      {target ? (
        children
      ) : (
        <div ref={scrollRef} className={clsx("scroll-thin overflow-x-auto", scrollClassName)}>
          {children}
        </div>
      )}
      {/* A narrow fade over the cut edge, and the word on a solid tab at the top of it —
          the fade alone is easy to miss, and a full-height label would hide data. */}
      <span
        aria-hidden
        className={clsx(
          "pointer-events-none absolute transition-opacity duration-150",
          cue.more ? "opacity-100" : "opacity-0",
          cueClassName,
        )}
        style={{ right: cue.right, top: cue.top, height: cue.height }}
      >
        <span
          className={clsx(
            "absolute inset-y-0 right-0 w-10 bg-linear-to-l to-transparent",
            SURFACE[surface].fade,
          )}
        />
        <span
          className={clsx(
            "absolute right-0 whitespace-nowrap rounded-l-md pl-2 pr-0.5 text-[11.5px] font-medium leading-none text-signal",
            SURFACE[surface].tab,
            target ? "top-1/2 -translate-y-1/2" : "top-0 py-1",
          )}
        >
          {label ? `${label} →` : "→"}
        </span>
      </span>
    </div>
  );
}
