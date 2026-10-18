/**
 * Court geometry as product furniture.
 *
 * Every shape here is real basketball geometry used for a functional purpose — the
 * half-court frames an empty state, the free-throw key holds a verdict, the lane carries
 * assets between teams. It is drawn the way a coach draws it on a whiteboard: fine chalk
 * lines, one mint stroke where something is live, nothing filled in.
 */

/** Half-court in true proportions (50ft wide × 47ft deep), drawn as fine chalk line art. */
export function HalfCourt({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 500 470"
      className={className}
      aria-hidden
      fill="none"
      preserveAspectRatio="xMidYMax meet"
    >
      <g
        stroke="currentColor"
        strokeWidth="1"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      >
        {/* sidelines + baseline */}
        <rect x="1" y="1" width="498" height="468" rx="2" opacity="0.4" />
        {/* the key (16ft lane), free-throw circle — its far half dashed, as on the floor */}
        <rect x="170" y="310" width="160" height="159" opacity="0.7" />
        <path d="M190 310a60 60 0 0 1 120 0" opacity="0.7" />
        <path d="M190 310a60 60 0 0 0 120 0" opacity="0.45" strokeDasharray="6 7" />
        {/* lane hash marks */}
        <path d="M170 380h-8M170 410h-8M170 440h-8M330 380h8M330 410h8M330 440h8" opacity="0.5" />
        {/* restricted arc, rim and backboard */}
        <path d="M210 449a40 40 0 0 1 80 0" opacity="0.55" />
        <circle cx="250" cy="440" r="7.5" opacity="0.8" />
        <line x1="226" y1="455" x2="274" y2="455" opacity="0.8" />
        {/* three-point line: corners then arc */}
        <path d="M30 469V330" opacity="0.6" />
        <path d="M470 469V330" opacity="0.6" />
        <path d="M30 330a222 222 0 0 1 440 0" opacity="0.6" />
        {/* centre-court arc */}
        <path d="M190 1a60 60 0 0 0 120 0" opacity="0.35" />
      </g>
    </svg>
  );
}

/**
 * The free-throw key, used as a container shape for headline verdicts: a fine chalk arc
 * sits on the top edge of the block like the top of the key, in the verdict's colour.
 */
export function KeyFrame({
  children,
  accent = "var(--signal)",
  className,
}: {
  children: React.ReactNode;
  accent?: string;
  className?: string;
}) {
  return (
    <div className={`relative ${className ?? ""}`}>
      <span aria-hidden className="pointer-events-none absolute inset-x-0 -top-px flex justify-center">
        <svg viewBox="0 0 96 14" className="h-[14px] w-[96px]" fill="none">
          <path d="M8 0.5a40 13 0 0 0 80 0" stroke={accent} strokeOpacity="0.6" strokeWidth="1" />
        </svg>
      </span>
      {children}
    </div>
  );
}

/**
 * The transaction lane: the strip between two team workspaces that carries assets. Drawn
 * as a chalk centre line through a centre circle; the circle's dot turns mint while assets
 * are crossing. Direction is encoded by the arrow heads AND by the label text beside the
 * lane, never by colour alone.
 */
export function TransactionLane({
  leftColor = "var(--signal)",
  rightColor = "var(--series-b)",
  active = false,
  orientation = "horizontal",
  className,
}: {
  leftColor?: string;
  rightColor?: string;
  active?: boolean;
  orientation?: "horizontal" | "vertical";
  className?: string;
}) {
  const ring = active ? "var(--signal)" : "var(--chalk-faint)";
  if (orientation === "vertical") {
    return (
      <svg viewBox="0 0 40 120" className={className} aria-hidden fill="none">
        <line x1="20" y1="2" x2="20" y2="118" stroke="var(--hairline)" strokeWidth="1" />
        <circle cx="20" cy="60" r="12" stroke={ring} strokeOpacity={active ? 0.8 : 0.45} strokeWidth="1" fill="var(--court-black)" />
        <circle cx="20" cy="60" r="2.5" fill={active ? "var(--signal)" : "var(--chalk-faint)"} opacity={active ? 1 : 0.6} />
        <path
          d="M14 18v18m0 0-3.5-3.5M14 36l3.5-3.5"
          stroke={rightColor}
          strokeWidth="1.25"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M26 102V84m0 0-3.5 3.5M26 84l3.5 3.5"
          stroke={leftColor}
          strokeWidth="1.25"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 120 44" className={className} aria-hidden fill="none">
      <line x1="2" y1="22" x2="118" y2="22" stroke="var(--hairline)" strokeWidth="1" />
      <circle cx="60" cy="22" r="12" stroke={ring} strokeOpacity={active ? 0.8 : 0.45} strokeWidth="1" fill="var(--court-black)" />
      <circle cx="60" cy="22" r="2.5" fill={active ? "var(--signal)" : "var(--chalk-faint)"} opacity={active ? 1 : 0.6} />
      {/* rightward path */}
      <path
        d="M78 15h22m0 0-4-3.5m4 3.5-4 3.5"
        stroke={rightColor}
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* leftward path */}
      <path
        d="M42 29H20m0 0 4-3.5M20 29l4 3.5"
        stroke={leftColor}
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Basketball seams — used at small sizes as the "ball" glyph in the brand system. */
export function BallGlyph({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-hidden fill="none">
      <circle cx="12" cy="12" r="10.25" stroke="currentColor" strokeWidth="1.3" />
      <path d="M12 1.75v20.5M1.75 12h20.5" stroke="currentColor" strokeWidth="1" opacity="0.7" />
      <path
        d="M4.6 4.6c3.9 3.2 3.9 11.6 0 14.8M19.4 4.6c-3.9 3.2-3.9 11.6 0 14.8"
        stroke="currentColor"
        strokeWidth="1"
        opacity="0.7"
      />
    </svg>
  );
}

/**
 * Retired. Pivot has no shot-location data, so a shot-chart scatter would present an
 * invented distribution as basketball fact. The export is kept so an old import still
 * compiles; it draws nothing.
 */
export function ShotChartMotif({ className }: { className?: string }) {
  return <svg viewBox="0 0 500 470" className={className} aria-hidden fill="none" />;
}
