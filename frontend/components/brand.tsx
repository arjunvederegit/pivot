/**
 * Pivot brand.
 *
 * The official Classic logo — a white serif "Pivot" whose lowercase "o" is a fine-line
 * basketball — ships as `public/brand/pivot-logo.svg`, traced from the supplied artwork on
 * a transparent ground. It is never retyped or approximated: every placement renders that
 * one asset, scaled by height so the proportions cannot drift.
 */

export const PRODUCT_NAME = "Pivot";
export const PRODUCT_TAGLINE = "Basketball Intelligence for Better Decisions";

/** The logo's native aspect ratio (752 × 281). */
const LOGO_RATIO = 752 / 281;

export function PivotLogo({ height = 30, className }: { height?: number; className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static brand SVG, sized by height
    <img
      src="/brand/pivot-logo.svg"
      alt={PRODUCT_NAME}
      height={height}
      width={Math.round(height * LOGO_RATIO)}
      className={className}
      style={{ height, width: "auto" }}
      draggable={false}
    />
  );
}

/** The basketball "o" on its own, from the same traced artwork — for the collapsed rail and favicon. */
export function PivotMark({ size = 32, className }: { size?: number; className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static brand SVG
    <img
      src="/brand/pivot-mark.svg"
      alt={PRODUCT_NAME}
      width={size}
      height={size}
      className={className}
      draggable={false}
    />
  );
}
