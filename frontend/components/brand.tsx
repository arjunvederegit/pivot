/**
 * Pivot mark: a basketball drawn as a single fine line, with the pivot foot — the one
 * planted point a move turns on — marked in mint. Quiet enough to sit beside a serif
 * wordmark, distinct enough to stand alone as the favicon.
 */

export const PRODUCT_NAME = "Pivot";
export const PRODUCT_TAGLINE = "Basketball Intelligence for Better Decisions";

export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      role="img"
      aria-label={PRODUCT_NAME}
      className="shrink-0"
      fill="none"
    >
      <rect x="0.5" y="0.5" width="39" height="39" rx="10" fill="#1c2220" stroke="#2d3531" />
      <g stroke="#f0eee8" strokeWidth="1.5" strokeLinecap="round">
        <circle cx="20" cy="20" r="11" />
        <path d="M20 9v22M9 20h22" opacity="0.7" />
        <path d="M12.6 12.4c3.4 4.1 3.4 11.1 0 15.2M27.4 12.4c-3.4 4.1-3.4 11.1 0 15.2" opacity="0.7" />
      </g>
      <circle cx="20" cy="20" r="3" fill="#86c9a5" />
    </svg>
  );
}

export function BrandWordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex min-w-0 flex-col">
      <span className="font-serif text-[1.6rem] leading-none text-foreground">Pivot</span>
      {!compact && (
        <span className="mt-1 truncate text-[10px] font-medium uppercase tracking-[0.12em] text-faint">
          Basketball intelligence
        </span>
      )}
    </span>
  );
}
