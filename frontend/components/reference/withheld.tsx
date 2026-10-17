/**
 * A value Pivot does not have, drawn as what it is: an empty slot.
 *
 * The slot is hatched and dashed so a reader can tell "not on file" from "zero" at a glance,
 * and it always carries the word that says why — the hatching is never the only signal.
 * Used where a ledger would normally hold money (the Cap Center's contract sheet).
 *
 * Server-safe: no hooks.
 */

import clsx from "clsx";

/** Fine diagonal hatching in chalk at a whisper of opacity — the "withheld" texture. */
export const HATCH: React.CSSProperties = {
  backgroundImage: "repeating-linear-gradient(135deg, rgb(240 238 232 / 0.05) 0 1px, transparent 1px 6px)",
};

export function Withheld({
  children,
  className,
  title,
}: {
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center whitespace-nowrap rounded-md border border-dashed border-unavail/45 px-2 py-[3px] text-[12px] leading-tight text-faint",
        className,
      )}
      style={HATCH}
    >
      {children}
    </span>
  );
}
