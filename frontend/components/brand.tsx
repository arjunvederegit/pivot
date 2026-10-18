/**
 * Pivot brand.
 *
 * The official Pivot logo — `public/brand/pivot-logo.png`: rounded white lettering with an orange
 * basketball (black outline, white seams) for the "o". It is built from the approved artwork by
 * `scripts/brand/build_logo.py` (lettering de-noised and re-filled, ball kept pixel-for-pixel). It is
 * never retyped: every placement renders that one asset, scaled by height so proportions cannot drift.
 */

export const PRODUCT_NAME = "Pivot";
export const PRODUCT_TAGLINE = "Basketball Intelligence for Better Decisions";

/** The logo's native aspect ratio (1519 × 472). */
const LOGO_RATIO = 1519 / 472;

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
