"use client";

/**
 * The scroll engine behind the Pivot narrative homepage.
 *
 * **It never takes the scroll away from you.** There is no `preventDefault`, no wheel
 * capture, no programmatic scrolling and no mandatory snapping — the page scrolls exactly
 * as the browser intends. The cinematic feel comes from `position: sticky`: an act is a
 * tall section holding a stage that parks at the top of the viewport while the section
 * passes behind it. Native scroll, keyboard (space, PageDown, arrows), trackpad, momentum
 * and browser find-in-page all keep working, and nothing can trap you mid-act.
 *
 * **It does not re-render React while you scroll.** One `requestAnimationFrame` loop, one
 * listener for the whole page, writes a `--p` custom property (0 → 1) on each registered
 * stage. Everything else animates from that value in CSS, on transform and opacity only.
 * Scrolling the entire page costs a handful of style writes per frame.
 *
 * **Progress is decoration; content is not gated behind it.** Text reveals are driven by a
 * one-way IntersectionObserver (`data-seen`), so once something has appeared it stays —
 * scrolling back up never hides it, and nothing focusable is left invisible.
 */

import { useEffect, useRef, useState } from "react";

type Registration = {
  section: HTMLElement;
  stage: HTMLElement;
};

const registry = new Set<Registration>();
let frame = 0;
let listening = false;

const clamp = (n: number, lo: number, hi: number) => (n < lo ? lo : n > hi ? hi : n);

function measure() {
  frame = 0;
  const vh = window.innerHeight;
  for (const reg of registry) {
    const rect = reg.section.getBoundingClientRect();
    // How far the section can travel while its stage stays pinned.
    const travel = rect.height - vh;
    const p = travel <= 0 ? (rect.top <= 0 ? 1 : 0) : clamp(-rect.top / travel, 0, 1);
    reg.stage.style.setProperty("--p", p.toFixed(4));
  }
}

function schedule() {
  if (!frame) frame = requestAnimationFrame(measure);
}

function listen() {
  if (listening) return;
  listening = true;
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule, { passive: true });
}

function unlisten() {
  if (!listening || registry.size) return;
  listening = false;
  window.removeEventListener("scroll", schedule);
  window.removeEventListener("resize", schedule);
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
}

/**
 * Registers one act with the shared loop.
 *
 * Returns two refs: the tall section that provides the scroll distance, and the stage that
 * sticks inside it and receives `--p`. Deliberately returns no React state — an act that
 * needed to re-render on every frame would defeat the point.
 */
export function useAct() {
  const section = useRef<HTMLElement | null>(null);
  const stage = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const sectionEl = section.current;
    const stageEl = stage.current;
    if (!sectionEl || !stageEl) return;

    const reg: Registration = { section: sectionEl, stage: stageEl };
    registry.add(reg);
    listen();
    schedule();

    return () => {
      registry.delete(reg);
      unlisten();
    };
  }, []);

  return { section, stage };
}

/**
 * One-way reveal. Adds `data-seen="true"` the first time the element enters the viewport
 * and then stops observing, so content never disappears on the way back up.
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (el.dataset.seen === "true") return;

    // No IntersectionObserver (or reduced motion handled in CSS) — show it immediately.
    if (typeof IntersectionObserver === "undefined") {
      el.dataset.seen = "true";
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            el.dataset.seen = "true";
            observer.disconnect();
          }
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.12 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return ref;
}

/**
 * True once the element has come within `rootMargin` of the viewport, and true forever
 * after. Used to enable an act's queries only when the reader is approaching it, so
 * landing on the hero does not fetch the whole narrative.
 */
export function useNear<T extends HTMLElement = HTMLDivElement>(rootMargin = "60% 0px") {
  const ref = useRef<T | null>(null);
  const [near, setNear] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || near) return;
    if (typeof IntersectionObserver === "undefined") {
      // No observer (an old browser, or a non-DOM test environment): load on mount
      // rather than never. Deferred so it is not a synchronous cascade in this commit.
      const id = setTimeout(() => setNear(true), 0);
      return () => clearTimeout(id);
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          observer.disconnect();
        }
      },
      { rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [near, rootMargin]);

  return { ref, near };
}
