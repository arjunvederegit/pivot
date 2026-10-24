"""The player-season panel and its as-of views.

**The rule this module exists to enforce:** a prediction made at an information cutoff may depend
only on rows whose season is at or before the cutoff. Rather than asking every feature function to
remember that, the panel hands out ``AsOfPanel`` objects that physically do not contain later
seasons. Feature code takes an ``AsOfPanel``; it is never given the full ``PlayerSeasonPanel``.

Two further leakage routes are closed here rather than left to callers:

* ``is_active``, ``position`` and ``height_inches`` on the legacy feature frame describe the player
  *today* (``is_active`` is false for anyone who has since left the league). They are removed from
  every as-of view. Team membership is the season's own ``player_season_stats.team_id`` — never
  ``players``/``rosters``, which hold the present.
* Units. A fraction column holding percentages (55.0 for 0.55) would train happily and be wrong by
  two orders of magnitude, so ``validate_panel`` rejects it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.canonical.seasons import InvalidSeasonError, Season

#: Properties of the player as of *now*. Never allowed into a historical feature frame.
CURRENT_STATE_COLUMNS = ("is_active", "position", "height_inches")

REQUIRED_COLUMNS = ("player_id", "season", "GP", "MIN", "total_minutes", "team_id", "AGE")

#: Columns expressed as fractions of 1.0, with the largest value a *rotation* player (>= 200 minutes)
#: can plausibly have. Fringe rows are excluded from the check: one made three on one attempt is a
#: legitimate 1.5 true-shooting percentage. A rotation value above the bound means percentages.
FRACTION_COLUMNS = {
    "TS_PCT": 1.0,
    "EFG_PCT": 1.0,
    "USG_PCT": 1.0,
    "AST_PCT": 1.0,
    "REB_PCT": 1.0,
    "OREB_PCT": 1.0,
    "DREB_PCT": 1.0,
}

#: Columns NBA.com publishes in percentage points (0-100). ``TM_TOV_PCT`` is the one in this
#: panel: every other advanced rate above is a fraction. The incumbent z-scores it, which makes the
#: unit irrelevant to TEI, but a model that read it raw beside the fractions would be wrong by 100x.
PERCENT_COLUMNS = ("TM_TOV_PCT",)
ROTATION_MINUTES = 200.0


class LeakageError(AssertionError):
    """Information from after the cutoff reached a place it must not."""


class PanelValidationError(ValueError):
    """The panel is not fit to train or evaluate on. The message lists every problem found."""


def season_key(label: str) -> int:
    return Season.parse(label).start_year


def season_label(start_year: int) -> str:
    return Season(start_year).label


def validate_panel(df: pd.DataFrame) -> None:
    """Raise ``PanelValidationError`` unless the frame is one row per (player, season) with sane units."""
    problems: list[str] = []
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise PanelValidationError(f"missing required columns: {missing}")
    if df["player_id"].isna().any() or (df["player_id"].astype(str).str.len() == 0).any():
        problems.append(f"{int(df['player_id'].isna().sum())} rows have no player identity")
    bad_labels = []
    for label in df["season"].dropna().unique():
        try:
            Season.parse(str(label))
        except InvalidSeasonError:
            bad_labels.append(label)
    if bad_labels:
        problems.append(f"unparseable season labels: {sorted(map(str, bad_labels))[:5]}")
    duplicated = df.duplicated(["player_id", "season"], keep=False)
    if duplicated.any():
        problems.append(
            f"{int(duplicated.sum())} rows duplicate a (player, season) — a traded player must be "
            "one row, not one per team"
        )
    gp = pd.to_numeric(df["GP"], errors="coerce")
    if gp.isna().any() or (gp < 0).any() or (gp > 90).any():
        problems.append("GP outside [0, 90] or missing")
    per_game = pd.to_numeric(df["MIN"], errors="coerce")
    if per_game.isna().any() or (per_game < 0).any() or (per_game > 48.5).any():
        problems.append("MIN (minutes per game) outside [0, 48.5] — season totals mixed with per-game?")
    expected_total = gp * per_game
    total = pd.to_numeric(df["total_minutes"], errors="coerce")
    if ((total - expected_total).abs() > 1e-6 * (1 + expected_total.abs())).any():
        problems.append("total_minutes != GP * MIN")
    rotation = df[pd.to_numeric(df["total_minutes"], errors="coerce") >= ROTATION_MINUTES]
    for col, bound in FRACTION_COLUMNS.items():
        if col in rotation.columns:
            values = pd.to_numeric(rotation[col], errors="coerce")
            if (values.abs() > bound).any():
                problems.append(
                    f"{col} has rotation-player values above {bound} — percentages where fractions are expected?"
                )
    for col in PERCENT_COLUMNS:
        if col in rotation.columns and len(rotation):
            median = float(pd.to_numeric(rotation[col], errors="coerce").median())
            if median < 1.0:
                problems.append(
                    f"{col} has a rotation median of {median:.3f}; NBA.com publishes it in percentage "
                    "points (expected roughly 10) — was it divided by 100?"
                )
    per_season = df.groupby("season").size()
    if len(per_season) >= 3:
        thin = per_season[per_season < 0.5 * per_season.median()]
        if len(thin):
            problems.append(
                f"season(s) {sorted(thin.index)} hold under half the median number of players "
                f"({int(per_season.median())}) — partially ingested?"
            )
    if problems:
        raise PanelValidationError("; ".join(problems))


@dataclass(frozen=True)
class AsOfPanel:
    """Everything known at the end of ``origin`` — and nothing later."""

    origin: str
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        self.assert_no_future()

    @property
    def seasons(self) -> list[str]:
        return sorted(self.frame["season"].unique(), key=season_key)

    def assert_no_future(self) -> None:
        cutoff = season_key(self.origin)
        if self.frame.empty:
            return
        latest = max(season_key(s) for s in self.frame["season"].unique())
        if latest > cutoff:
            raise LeakageError(
                f"an as-of view for {self.origin} contains season {season_label(latest)}"
            )
        banned = [c for c in CURRENT_STATE_COLUMNS if c in self.frame.columns]
        if banned:
            raise LeakageError(f"present-state columns in a historical view: {banned}")

    def window(self, n_seasons: int) -> list[str]:
        """The last ``n_seasons`` consecutive season labels ending at the origin (those that exist)."""
        end = season_key(self.origin)
        labels = [season_label(y) for y in range(end - n_seasons + 1, end + 1)]
        have = set(self.seasons)
        return [s for s in labels if s in have]


@dataclass(frozen=True)
class PlayerSeasonPanel:
    """All seasons, one row per (player, season). Only ``as_of`` and ``season_rows`` expose rows."""

    frame: pd.DataFrame

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> PlayerSeasonPanel:
        df = df.drop(columns=[c for c in CURRENT_STATE_COLUMNS if c in df.columns]).copy()
        validate_panel(df)
        df = df.sort_values(["season", "player_id"], key=_sort_key).reset_index(drop=True)
        return cls(df)

    @classmethod
    def from_db(cls, db) -> PlayerSeasonPanel:  # type: ignore[no-untyped-def]
        from app.analytics.features import build_player_season_features

        frame = build_player_season_features(db)
        if frame.empty:
            raise PanelValidationError("no ingested player statistics")
        return cls.from_frame(frame)

    @property
    def seasons(self) -> list[str]:
        return sorted(self.frame["season"].unique(), key=season_key)

    def as_of(self, origin: str) -> AsOfPanel:
        cutoff = season_key(origin)
        keep = self.frame["season"].map(season_key) <= cutoff
        return AsOfPanel(origin=origin, frame=self.frame.loc[keep].copy())

    def season_rows(self, season: str) -> pd.DataFrame:
        """One season's rows — for building an *outcome*, never a feature."""
        return self.frame.loc[self.frame["season"] == season].copy()


def _sort_key(col: pd.Series) -> pd.Series:
    if col.name == "season":
        return col.map(season_key)
    return col


def replace_future(panel: PlayerSeasonPanel, after: str, rng: np.random.Generator) -> PlayerSeasonPanel:
    """A copy in which everything after ``after`` has been destroyed.

    For every later season: numeric statistics are permuted and rescaled; the rows are re-assigned
    to other players (identity permuted within the season) and other teams; and a third of them are
    deleted. Used by the leakage audit: if a fold's forecasts, scored population or eligibility
    change when the future is destroyed, the fold read the future — including through *which
    players exist* or *where they played*, not just through their statistics."""
    frame = panel.frame.copy()
    later = frame["season"].map(season_key) > season_key(after)
    protected = {"player_id", "season", "team_id", "full_name", "nba_player_id"}
    for col in frame.columns:
        if col in protected or not pd.api.types.is_numeric_dtype(frame[col]):
            continue
        scrambled = rng.permutation(frame.loc[later, col].to_numpy(dtype=float)) * rng.uniform(0.5, 1.5)
        frame[col] = frame[col].astype(float)
        frame.loc[later, col] = scrambled
    frame.loc[later, "total_minutes"] = frame.loc[later, "GP"] * frame.loc[later, "MIN"]
    for season in frame.loc[later, "season"].unique():
        rows = frame.index[frame["season"] == season]
        for col in ("player_id", "team_id"):
            frame.loc[rows, col] = rng.permutation(frame.loc[rows, col].to_numpy())
    drop = frame.index[later][rng.random(int(later.sum())) < 1 / 3]
    return PlayerSeasonPanel(frame.drop(index=drop).reset_index(drop=True))
