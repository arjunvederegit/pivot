/**
 * Pivot brand.
 *
 * The official Pivot logo — ships as `public/brand/pivot-logo.png` — the supplied artwork with its black ground keyed out
 * (scripts/brand/extract_logo.py), pixels otherwise untouched — a white serif wordmark with an
 * orange basketball for the "o". It is never retyped or approximated: every placement renders that
 * one asset, scaled by height so the proportions cannot drift.
 */

export const PRODUCT_NAME = "Pivot";
export const PRODUCT_TAGLINE = "Basketball Intelligence for Better Decisions";

/** The logo's native aspect ratio (1432 × 499). */
const LOGO_RATIO = 1432 / 499;

export function PivotLogo({ height = 30, className }: { height?: number; className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static brand asset, sized by height
    <img
      src="/brand/pivot-logo.png"
      alt={PRODUCT_NAME}
      height={height}
      width={Math.round(height * LOGO_RATIO)}
      className={className}
      style={{ height, width: "auto" }}
      draggable={false}
    />
  );
}

/** The basketball "o" on its own, cropped from the same artwork — for the collapsed rail and favicon. */
export function PivotMark({ size = 32, className }: { size?: number; className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static brand asset
    <img
      src="/brand/pivot-mark.png"
      alt={PRODUCT_NAME}
      width={size}
      height={size}
      className={className}
      draggable={false}
    />
  );
}
