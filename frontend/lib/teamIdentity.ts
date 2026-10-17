/**
 * The single source of team identity for the whole product.
 *
 * Page components must never re-declare team colors, abbreviations or logo
 * paths — import from here. Colors are the franchise's own; `bright` is the
 * variant tuned for legibility on the arena-navy surfaces, and `contrast` is
 * the text color that reads on top of `primary`.
 *
 * Team color is always a *contextual* accent (borders, active states, chart
 * series, badges, panel edges) — never a full-page recolor, and never the only
 * channel carrying meaning.
 */

export interface TeamIdentity {
  abbreviation: string;
  nbaTeamId: number;
  name: string;
  nickname: string;
  conference: "East" | "West";
  primary: string;
  secondary: string;
  /** Legible-on-dark variant, used for text, borders and chart series. */
  bright: string;
  /** Text color that reads on top of `primary`. */
  contrast: string;
}

const T = (
  abbreviation: string,
  nbaTeamId: number,
  name: string,
  nickname: string,
  conference: "East" | "West",
  primary: string,
  secondary: string,
  bright: string,
  contrast = "#FFFFFF",
): TeamIdentity => ({
  abbreviation,
  nbaTeamId,
  name,
  nickname,
  conference,
  primary,
  secondary,
  bright,
  contrast,
});

export const TEAMS: Record<string, TeamIdentity> = {
  ATL: T("ATL", 1610612737, "Atlanta Hawks", "Hawks", "East", "#E03A3E", "#C1D32F", "#F05055"),
  BOS: T("BOS", 1610612738, "Boston Celtics", "Celtics", "East", "#007A33", "#BA9653", "#22C55E"),
  BKN: T("BKN", 1610612751, "Brooklyn Nets", "Nets", "East", "#3F4854", "#FFFFFF", "#AEBACB"),
  CHA: T("CHA", 1610612766, "Charlotte Hornets", "Hornets", "East", "#1D1160", "#00788C", "#22C9E0"),
  CHI: T("CHI", 1610612741, "Chicago Bulls", "Bulls", "East", "#CE1141", "#8C9096", "#F2506F"),
  CLE: T("CLE", 1610612739, "Cleveland Cavaliers", "Cavaliers", "East", "#860038", "#FDBB30", "#FDBB30"),
  DAL: T("DAL", 1610612742, "Dallas Mavericks", "Mavericks", "West", "#00538C", "#B8C4CA", "#3B9BE0"),
  DEN: T("DEN", 1610612743, "Denver Nuggets", "Nuggets", "West", "#0E2240", "#FEC524", "#FEC524"),
  DET: T("DET", 1610612765, "Detroit Pistons", "Pistons", "East", "#C8102E", "#1D42BA", "#EF4E63"),
  GSW: T("GSW", 1610612744, "Golden State Warriors", "Warriors", "West", "#1D428A", "#FFC72C", "#FFC72C"),
  HOU: T("HOU", 1610612745, "Houston Rockets", "Rockets", "West", "#CE1141", "#9EA2A2", "#F2506F"),
  IND: T("IND", 1610612754, "Indiana Pacers", "Pacers", "East", "#002D62", "#FDBB30", "#FDBB30"),
  LAC: T("LAC", 1610612746, "LA Clippers", "Clippers", "West", "#C8102E", "#1D428A", "#EF4E63"),
  LAL: T("LAL", 1610612747, "Los Angeles Lakers", "Lakers", "West", "#552583", "#FDB927", "#FDB927"),
  MEM: T("MEM", 1610612763, "Memphis Grizzlies", "Grizzlies", "West", "#5D76A9", "#12173F", "#8FA8DA"),
  MIA: T("MIA", 1610612748, "Miami Heat", "Heat", "East", "#98002E", "#F9A01B", "#F9A01B"),
  MIL: T("MIL", 1610612749, "Milwaukee Bucks", "Bucks", "East", "#00471B", "#EEE1C6", "#1FA34D"),
  MIN: T("MIN", 1610612750, "Minnesota Timberwolves", "Timberwolves", "West", "#0C2340", "#78BE20", "#78BE20"),
  NOP: T("NOP", 1610612740, "New Orleans Pelicans", "Pelicans", "West", "#0C2340", "#C8102E", "#D3A44E"),
  NYK: T("NYK", 1610612752, "New York Knicks", "Knicks", "East", "#006BB6", "#F58426", "#F58426"),
  OKC: T("OKC", 1610612760, "Oklahoma City Thunder", "Thunder", "West", "#007AC1", "#EF3B24", "#38ADE8"),
  ORL: T("ORL", 1610612753, "Orlando Magic", "Magic", "East", "#0077C0", "#C4CED4", "#38A6E8"),
  PHI: T("PHI", 1610612755, "Philadelphia 76ers", "76ers", "East", "#006BB6", "#ED174C", "#3B9BE0"),
  PHX: T("PHX", 1610612756, "Phoenix Suns", "Suns", "West", "#1D1160", "#E56020", "#E56020"),
  POR: T("POR", 1610612757, "Portland Trail Blazers", "Trail Blazers", "West", "#E03A3E", "#8F8F8F", "#F05055"),
  SAC: T("SAC", 1610612758, "Sacramento Kings", "Kings", "West", "#5A2D81", "#63727A", "#9B6FD6"),
  SAS: T("SAS", 1610612759, "San Antonio Spurs", "Spurs", "West", "#6B7280", "#C4CED4", "#C4CED4"),
  TOR: T("TOR", 1610612761, "Toronto Raptors", "Raptors", "East", "#CE1141", "#A1A1A4", "#F2506F"),
  UTA: T("UTA", 1610612762, "Utah Jazz", "Jazz", "West", "#002B5C", "#F9A01B", "#F9A01B"),
  WAS: T("WAS", 1610612764, "Washington Wizards", "Wizards", "East", "#002B5C", "#E31837", "#E8566B"),
};

const NEUTRAL: TeamIdentity = {
  abbreviation: "NBA",
  nbaTeamId: 0,
  name: "Unknown team",
  nickname: "Team",
  conference: "East",
  primary: "#334155",
  secondary: "#64748b",
  bright: "#94a3b8",
  contrast: "#FFFFFF",
};

export function teamIdentity(abbreviation: string | null | undefined): TeamIdentity {
  if (!abbreviation) return NEUTRAL;
  return TEAMS[abbreviation.toUpperCase()] ?? NEUTRAL;
}

/** Backend-served logo endpoint (assets are indexed locally by NBA identity). */
export function teamLogoSrc(abbreviation: string | null | undefined): string | null {
  if (!abbreviation) return null;
  return `/api/v1/assets/teams/${abbreviation.toUpperCase()}`;
}

export function playerPhotoSrc(nbaPlayerId: number | null | undefined): string | null {
  if (!nbaPlayerId) return null;
  return `/api/v1/assets/players/${nbaPlayerId}`;
}

/** CSS custom properties scoping a subtree to one team's colors. */
export function teamVars(abbreviation: string | null | undefined): React.CSSProperties {
  const t = teamIdentity(abbreviation);
  return {
    "--team-primary": t.primary,
    "--team-secondary": t.secondary,
    "--team-bright": t.bright,
    "--team-contrast": t.contrast,
    "--edge": t.bright,
  } as React.CSSProperties;
}

export const ALL_TEAM_ABBREVIATIONS = Object.keys(TEAMS);
