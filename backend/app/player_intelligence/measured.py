"""Measured records: what a player demonstrably did, with the sample behind it.

A measured record is an observation, not an estimate: it has no model, no interval and no
forecast period. What can go wrong with one is a *data* problem — the wrong season, a rate in
percentage points, a count that does not reconcile with the published rate, a player with no
attempts shown as shooting 0% — so this module checks integrity before it speaks, and when a check
fails it says so (``Unavailable``) instead of serving the number.

Rules enforced here, each with a test:

* **No attempts, no rate.** A player who never shot a three has no three-point accuracy. The
  quantity is unavailable; it is never 0.
* **Units.** Rates are fractions of 1. A published fraction outside [0, 1] is rejected, not rescaled.
* **Reconciliation.** True shooting recomputed from the recovered counts must agree with the
  published ``TS_PCT`` to its three-decimal precision; if it does not, the published value is
  served with a limitation, never silently replaced.
* **Season and scope.** Only regular-season rows are read; a record is for exactly the season it
  names; a missing season is reported as missing, never filled.
* **Team changes.** NBA.com publishes one row per player-season with ``TEAM_COUNT``; a player on
  several teams has season totals across them, and the record says so.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.seasons import InvalidSeasonError, Season
from app.db.models import PlayerSeasonStats
from app.modeling.contract import (
    EvaluationStatus,
    OutputKind,
    Period,
    PlayerIntelligenceRecord,
    Provenance,
    Sample,
    SourceRef,
)

from .components import QUALITY_UNCONSTRAINED, Reconstruction, reconstruct
from .quantities import QUANTITIES, get_quantity

TS_RECONCILIATION_TOLERANCE = 0.0006  # published to 3 decimals: half a unit in the last place + slack
MAX_GAMES = 90
RECONSTRUCTION_NOTE_SHARE = 0.02  # mention reconstruction ambiguity only when it is >= 2% of the attempts


@dataclass(frozen=True)
class Unavailable:
    """A quantity (or kind) the engine will not return, and why. Part of the answer, not an error."""

    quantity: str
    kind: str
    reason: str
    requirement: str | None = None
    season: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class SeasonRow:
    season: str
    games: int
    minutes_per_game: float | None
    team_id: str | None
    team_count: int | None
    base: dict
    advanced: dict
    retrieved_at: datetime | None
    recon: Reconstruction | None

    @property
    def total_minutes(self) -> float | None:
        return None if self.minutes_per_game is None else self.games * self.minutes_per_game

    def snapshot_id(self) -> str:
        blob = json.dumps({"b": self.base, "a": self.advanced, "s": self.season}, sort_keys=True, default=str)
        return "ds-" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def load_season_rows(db: Session, player_id: str, as_of: str | None = None) -> dict[str, SeasonRow]:
    """Regular-season rows for one player, at or before ``as_of`` (the information cutoff)."""
    cutoff = Season.parse(as_of).start_year if as_of else None
    rows = db.execute(
        select(PlayerSeasonStats)
        .where(
            PlayerSeasonStats.player_id == player_id,
            PlayerSeasonStats.season_type == "regular",
            PlayerSeasonStats.stat_type.in_(("base", "advanced")),
        )
    ).scalars().all()
    by_season: dict[str, dict] = {}
    for r in rows:
        try:
            start = Season.parse(r.season).start_year
        except InvalidSeasonError:
            continue
        if cutoff is not None and start > cutoff:
            continue
        slot = by_season.setdefault(r.season, {})
        slot[r.stat_type] = r
    out: dict[str, SeasonRow] = {}
    for season, slot in by_season.items():
        base_row = slot.get("base")
        if base_row is None or base_row.games_played is None:
            continue
        base, adv = base_row.stats or {}, (slot.get("advanced").stats if slot.get("advanced") else {}) or {}
        gp = int(base_row.games_played)
        recon = reconstruct(base, adv, gp) if gp > 0 else None
        retrieved = max((x.source_retrieved_at for x in slot.values() if x.source_retrieved_at), default=None)
        team_count = base.get("TEAM_COUNT")
        out[season] = SeasonRow(
            season=season, games=gp, minutes_per_game=base_row.minutes, team_id=base_row.team_id,
            team_count=int(team_count) if isinstance(team_count, (int, float)) and not math.isnan(team_count) else None,
            base=base, advanced=adv, retrieved_at=retrieved, recon=recon,
        )
    return out


def _fraction(value: object) -> float | None:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _source(row: SeasonRow, dataset: str) -> SourceRef:
    return SourceRef(provider="nba_api", dataset=dataset, retrieved_at=row.retrieved_at)


def _common_limitations(row: SeasonRow) -> list[str]:
    notes = []
    if row.team_count and row.team_count > 1:
        notes.append(
            f"the player appeared for {row.team_count} teams this season; the figures are season totals across all of them"
        )
    if row.games < 10:
        notes.append(f"only {row.games} games played: a very small sample")
    return notes


def _sample(row: SeasonRow, *, attempts: float | None = None, makes: float | None = None, unit: str | None = None) -> Sample:
    return Sample(
        games=row.games, minutes=row.total_minutes, attempts=attempts, makes=makes, unit=unit,
        counts_quality=(row.recon.quality if row.recon else None) if attempts is not None else None,
    )


def _record(player_id: str, quantity: str, value: float, row: SeasonRow, sample: Sample, dataset: str, limitations: list[str]) -> PlayerIntelligenceRecord:
    spec = get_quantity(quantity)
    return PlayerIntelligenceRecord(
        player_id=player_id, quantity=quantity, unit=spec.unit, output_kind=OutputKind.MEASURED, value=value,
        period=Period(season=row.season, as_of_season=row.season), sample=sample,
        provenance=Provenance(sources=[_source(row, dataset)], data_snapshot_id=row.snapshot_id()),
        assumptions=[spec.definition],
        limitations=limitations + _common_limitations(row),
    )


def measured_record(player_id: str, quantity: str, row: SeasonRow) -> PlayerIntelligenceRecord | Unavailable:
    """One measured record for one player-season — or the reason there is none."""
    spec = get_quantity(quantity)
    rec = row.recon
    if quantity in ("ts_pct", "fg3_pct", "ft_pct"):
        if rec is None:
            return Unavailable(quantity, "measured", "shot counts could not be recovered from the stored statistics", season=row.season)
        if quantity == "fg3_pct":
            m, a, unit = rec.fg3m, rec.fg3a, "three-point attempts"
        elif quantity == "ft_pct":
            m, a, unit = rec.ftm, rec.fta, "free-throw attempts"
        else:
            m, a, unit = None, rec.fga + 0.44 * rec.fta, "true shooting attempts (FGA + 0.44 FTA)"
        if a <= 0:
            return Unavailable(
                quantity, "measured",
                "no attempts this season, so the rate is undefined (it is not 0%)", season=row.season,
            )
        limits: list[str] = []
        if rec.quality == QUALITY_UNCONSTRAINED:
            return Unavailable(
                quantity, "measured",
                "integrity check failed: the stored per-game figures and published rates admit no consistent set of integer counts",
                season=row.season,
            )
        if rec.quality == "bounded":
            spread = (rec.fg3a_max - rec.fg3a_min) if quantity == "fg3_pct" else (rec.fta_max - rec.fta_min)
            # Material only when the unresolved attempts are a noticeable share of the sample.
            if spread / a >= RECONSTRUCTION_NOTE_SHARE:
                limits.append(
                    f"the provider's rounding leaves the exact attempt count uncertain by up to {spread} of {a:.0f} "
                    "(counts recovered by constraint reconstruction)"
                )
        if quantity == "ts_pct":
            published = _fraction(row.advanced.get("TS_PCT"))
            computed = rec.pts / (2 * a)
            if published is not None and not (0 <= published <= 1.0):
                return Unavailable(quantity, "measured", f"integrity check failed: published TS_PCT {published} is not a fraction of 1", season=row.season)
            if published is not None and abs(published - computed) > TS_RECONCILIATION_TOLERANCE:
                limits.append(
                    f"recomputed true shooting ({computed:.4f}) differs from the published value ({published:.4f}); the published value is shown"
                )
                value = published
            else:
                value = computed
        else:
            assert m is not None
            value = m / a
            if not 0.0 <= value <= 1.0:
                return Unavailable(quantity, "measured", f"integrity check failed: made/attempted = {value:.3f} is not a fraction of 1", season=row.season)
        return _record(
            player_id, quantity, float(value), row,
            _sample(row, attempts=float(a), makes=float(m) if m is not None else None, unit=unit),
            "leaguedashplayerstats/base+advanced", limits,
        )

    simple = {
        "ast_pct": ("AST_PCT", "advanced", True), "reb_pct": ("REB_PCT", "advanced", True),
        "oreb_pct": ("OREB_PCT", "advanced", True), "dreb_pct": ("DREB_PCT", "advanced", True),
        "usg_pct": ("USG_PCT", "advanced", True), "ast_to_ratio": ("AST_TO", "advanced", False),
        "minutes_per_game": ("MIN", "base", False),
    }
    if quantity in simple:
        key, table, is_fraction = simple[quantity]
        source = row.advanced if table == "advanced" else row.base
        v = _fraction(source.get(key))
        if quantity == "minutes_per_game" and v is None:
            v = row.minutes_per_game
        if v is None:
            return Unavailable(quantity, "measured", f"the provider did not publish {key} for this season", season=row.season)
        if is_fraction and not 0.0 <= v <= 1.0:
            return Unavailable(quantity, "measured", f"integrity check failed: {key} = {v} is not a fraction of 1 (percentage points?)", season=row.season)
        if quantity == "ast_to_ratio" and v < 0:
            return Unavailable(quantity, "measured", "integrity check failed: a negative ratio", season=row.season)
        limits = []
        if row.total_minutes is not None and row.total_minutes < 200 and is_fraction:
            limits.append(f"only {row.total_minutes:.0f} minutes: share statistics on this little playing time are unstable")
        return _record(
            player_id, quantity, float(v), row, _sample(row, unit="minutes" if is_fraction else None),
            f"leaguedashplayerstats/{table}", limits,
        )

    if quantity == "games_played":
        if not 0 <= row.games <= MAX_GAMES:
            return Unavailable(quantity, "measured", f"integrity check failed: {row.games} games is outside [0, {MAX_GAMES}]", season=row.season)
        return _record(
            player_id, quantity, float(row.games), row, _sample(row, unit="games"), "leaguedashplayerstats/base",
            ["a count of games, not a share of the schedule: 2019-20 and 2020-21 were shortened and teams played different numbers of games"],
        )
    raise KeyError(quantity)  # unreachable for registered quantities


def measured_for(
    player_id: str, quantities: list[str], rows: dict[str, SeasonRow], season: str
) -> tuple[list[PlayerIntelligenceRecord], list[Unavailable]]:
    """Measured records for one season, or one ``Unavailable`` per quantity when the season is absent."""
    records: list[PlayerIntelligenceRecord] = []
    missing: list[Unavailable] = []
    row = rows.get(season)
    for q in quantities:
        spec = QUANTITIES[q]
        if OutputKind.MEASURED not in spec.kinds:
            continue
        if row is None:
            earlier = sorted(rows, key=lambda s: Season.parse(s).start_year)
            hint = f"; the player's most recent season on or before this cutoff is {earlier[-1]}" if earlier else ""
            missing.append(Unavailable(q, "measured", f"no statistics for this player in {season}{hint}", season=season))
            continue
        out = measured_record(player_id, q, row)
        (missing if isinstance(out, Unavailable) else records).append(out)
    return records, missing


__all__ = ["EvaluationStatus", "SeasonRow", "Unavailable", "load_season_rows", "measured_for", "measured_record"]
