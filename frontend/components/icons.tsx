/**
 * Pivot line icons.
 *
 * One stroke weight (1.6 on a 24-unit grid), round joins, no fills — drawn for this
 * product rather than pulled from a general set, so the basketball-specific marks
 * (court, jersey, ball) sit naturally beside the generic ones. Every icon is decorative
 * by default: the control it sits in carries the accessible name.
 */

type IconProps = { size?: number; className?: string };

function Svg({
  size = 20,
  className,
  children,
}: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      {children}
    </svg>
  );
}

/** Home: the half-court — the place everything starts from. */
export function IconHome(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M8 20v-5.5h8V20" />
      <path d="M7.5 4a4.5 4.5 0 0 0 9 0" />
      <circle cx="12" cy="17.5" r="0.9" />
    </Svg>
  );
}

/** Scouting: a player file. */
export function IconScouting(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="10" cy="8" r="3.2" />
      <path d="M3.8 19.5c.9-3.4 3.4-5.3 6.2-5.3 1.4 0 2.7.5 3.8 1.3" />
      <circle cx="17.2" cy="16.8" r="2.6" />
      <path d="m19.1 18.7 1.9 1.9" />
    </Svg>
  );
}

/** Teams: a jersey. */
export function IconTeams(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M8.5 3.5 6 4.6 3.5 8.2 6 10l1-1.1V20.5h10V8.9l1 1.1 2.5-1.8L18 4.6l-2.5-1.1a3.6 3.6 0 0 1-7 0Z" />
      <path d="M10.2 12.5h3.6" />
    </Svg>
  );
}

/** Trade: assets crossing. */
export function IconTrade(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 8h13.5" />
      <path d="m14 4.5 3.5 3.5L14 11.5" />
      <path d="M20 16H6.5" />
      <path d="M10 12.5 6.5 16l3.5 3.5" />
    </Svg>
  );
}

/** Strategy: deals laid side by side. */
export function IconStrategy(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="3.5" y="4" width="7" height="16" rx="1.2" />
      <rect x="13.5" y="4" width="7" height="10" rx="1.2" />
      <path d="M13.5 18h7" />
    </Svg>
  );
}

/** Cap: a ledger. */
export function IconCap(p: IconProps) {
  return (
    <Svg {...p}>
      <rect x="4" y="3.5" width="16" height="17" rx="1.6" />
      <path d="M8 8h8M8 12h8M8 16h4.5" />
    </Svg>
  );
}

/** Methodology: an open book. */
export function IconMethod(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M12 6.5c-1.8-1.3-4.3-2-8-2v13c3.7 0 6.2.7 8 2 1.8-1.3 4.3-2 8-2v-13c-3.7 0-6.2.7-8 2Z" />
      <path d="M12 6.5v13" />
    </Svg>
  );
}

/** Data health: a pulse. */
export function IconHealth(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M3 12h4l2.2-5 3.6 10 2.4-5H21" />
    </Svg>
  );
}

/** About: information. */
export function IconInfo(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5.2M12 7.8v.2" />
    </Svg>
  );
}

/** Ask Pivot: a question put to the record — a speech bubble holding a cited line. */
export function IconAsk(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M5 5h14a1.5 1.5 0 0 1 1.5 1.5v8.5a1.5 1.5 0 0 1-1.5 1.5h-7.2L7.5 20v-3.5H5A1.5 1.5 0 0 1 3.5 15V6.5A1.5 1.5 0 0 1 5 5Z" />
      <path d="M7.5 9.2h9M7.5 12.3h5.2" />
      <path d="M15.6 12.3h.9" />
    </Svg>
  );
}

export function IconSearch(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.4-4.4" />
    </Svg>
  );
}

export function IconMenu(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M4 7h16M4 12h16M4 17h10" />
    </Svg>
  );
}

export function IconClose(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M6 6l12 12M18 6 6 18" />
    </Svg>
  );
}

export function IconCollapse(p: IconProps & { collapsed?: boolean }) {
  const { collapsed, ...rest } = p;
  return (
    <Svg {...rest}>
      <rect x="3.5" y="4" width="17" height="16" rx="1.6" />
      <path d="M9 4v16" />
      <path d={collapsed ? "m13 10 2 2-2 2" : "m15 10-2 2 2 2"} />
    </Svg>
  );
}

export function IconArrowRight(p: IconProps) {
  return (
    <Svg {...p}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </Svg>
  );
}

/** A basketball, for small brand and empty-state moments. */
export function IconBall(p: IconProps) {
  return (
    <Svg {...p}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 3.5v17M3.5 12h17" />
      <path d="M6 6c2.6 3.2 2.6 8.8 0 12M18 6c-2.6 3.2-2.6 8.8 0 12" />
    </Svg>
  );
}
