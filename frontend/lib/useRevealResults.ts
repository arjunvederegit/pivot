import { useEffect, useState } from "react";

/** How long after the results section has appeared before it is brought into view. */
export const REVEAL_DELAY_MS = 80;

/** One accepted evaluation request: its result, and whether that request still owns the screen. */
export interface RevealRequest<T> {
  result: T;
  isCurrent: () => boolean;
}

/**
 * Brings a new evaluation's results into view, once, for the request that produced them.
 *
 * `arm(request)` records the accepted request; nothing is scheduled yet. When the results section
 * for exactly that result is committed (`displayed` is the request's own result object, and the
 * section element has been handed to `sectionRef`), an effect starts the delay, so it is measured
 * from the section being there and never from the request completing. When it fires it asks the
 * request whether it still owns the evaluation (no newer evaluation has started, the context is the
 * committed one, the page is mounted) and that the element it captured is still in the document,
 * and scrolls THAT element, never an element looked up by id. The effect's cleanup cancels the timer
 * when the result changes, goes away, or the page unmounts; the check at execution does not depend on
 * that cleanup having run.
 *
 * Re-renders that change none of `pending`, `section` or `displayed` (a team selection, validation)
 * do not re-run the effect, so they neither restart the delay nor schedule a second reveal.
 */
export function useRevealResults<T>(displayed: T | null) {
  const [pending, setPending] = useState<RevealRequest<T> | null>(null);
  const [section, setSection] = useState<HTMLElement | null>(null);

  useEffect(() => {
    if (!pending || !section || displayed !== pending.result) return;
    const handle = setTimeout(() => {
      if (pending.isCurrent() && section.isConnected) {
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        section.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
      }
      // Retire only the request this timer was armed for, never armed twice. An obsolete timer must
      // not erase a newer reveal that has already been queued by a later accepted evaluation.
      setPending((current) => (current === pending ? null : current));
    }, REVEAL_DELAY_MS);
    return () => clearTimeout(handle);
  }, [pending, section, displayed]);

  return { arm: setPending, sectionRef: setSection };
}
