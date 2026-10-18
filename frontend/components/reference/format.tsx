/**
 * Presentation helpers shared by the reference pages (Salary-Cap Center, Data Health,
 * the saved-deal report) and the evaluation tabs.
 *
 * Kept out of `lib/format.ts` on purpose: these only decide how an already-served number
 * or sentence is *drawn*. Nothing here computes, rounds into, or substitutes a value.
 */

/**
 * A signed fixed-precision number whose sign comes from the ROUNDED value, so a tiny
 * negative never renders as "−0.00" (the defect `lib/format.tei` documents). Zero after
 * rounding carries no sign. Negative numbers use a true minus sign so columns align.
 */
export function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0) return (0).toFixed(digits);
  return rounded > 0 ? `+${rounded.toFixed(digits)}` : `−${Math.abs(rounded).toFixed(digits)}`;
}

/** Same sign rule as `signed`, for callers that colour or glyph by direction. */
export function direction(value: number | null | undefined, digits = 1): -1 | 0 | 1 {
  if (value === null || value === undefined || Number.isNaN(value)) return 0;
  const rounded = Number(value.toFixed(digits));
  return rounded > 0 ? 1 : rounded < 0 ? -1 : 0;
}

/**
 * Render a backend sentence with its `backtick` spans as inline code. The sentence is
 * the backend's own; only the code spans are restyled.
 */
export function CommandText({
  text,
  codeClassName = "rounded bg-foreground/8 px-1 py-px text-[0.88em] text-foreground",
}: {
  text: string;
  codeClassName?: string;
}) {
  const parts = text.split("`");
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code key={i} className={codeClassName}>
            {part}
          </code>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

/** Whole days between two ISO instants, or null when either is missing. */
export function daysBetween(fromIso: string | null | undefined, toIso: string | null | undefined) {
  if (!fromIso || !toIso) return null;
  const from = new Date(fromIso).getTime();
  const to = new Date(toIso).getTime();
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  return Math.max(0, Math.floor((to - from) / 86_400_000));
}
