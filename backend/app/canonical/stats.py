"""What a stored statistic *is*: its aggregation, its basis, and what "missing" means.

``player_season_stats.stat_type`` was doing four jobs at once — the NBA.com measure
(``base``/``advanced``), the provenance (``estimated`` is NBA's model, not a count), the
aggregation (``totals`` is a season sum, ``base`` is a per-game average) and the source
(``totals`` only ever comes from the user CSV). Nothing recorded any of it. The practical
consequence was measured on the development database: **the ``minutes`` column holds 34.8
for Nikola Jokić's ``base`` row and 2,265.0 for his ``totals`` row** — minutes per game and
minutes for the season, same column, no marker. A consumer that reads ``minutes`` without
filtering ``stat_type`` is wrong by a factor of games played, silently.

This module is the registry that closes that gap without touching the stored shape:

* ``spec_for(stat_type)`` says what a stored row means, and raises for a ``stat_type`` it
  does not know rather than guessing.
* ``require_comparable`` refuses to put per-game and season-total figures in one calculation.
* ``numeric``/``per_game_from_total`` encode the missing-value rule: an absent, NaN or
  non-numeric value is ``None`` — **never 0**. A rookie with no stat row has no points per
  game; he does not average zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Aggregation(StrEnum):
    PER_GAME = "per_game"
    TOTALS = "totals"


class Basis(StrEnum):
    """How a figure was arrived at. Carried on every payload that exposes statistics."""

    OBSERVED = "observed"  # counted from games by the provider
    ESTIMATED = "estimated"  # a provider's model output, not a count
    DERIVED = "derived"  # computed by Pivot from observed inputs
    MIXED = "mixed"  # a payload combining more than one of the above


class UnknownStatTypeError(KeyError):
    """A ``stat_type`` the registry has no definition for."""


class IncompatibleStatsError(ValueError):
    """An attempt to combine figures that do not measure the same thing."""


@dataclass(frozen=True)
class StatTypeSpec:
    stat_type: str
    measure: str
    aggregation: Aggregation
    basis: Basis
    #: The unit of the ``minutes`` column for rows of this type. The column is shared, the
    #: unit is not.
    minutes_unit: str
    description: str
    #: True for rows seeded by the synthetic demo league; never real NBA data.
    synthetic: bool = False


_SPECS: dict[str, StatTypeSpec] = {
    spec.stat_type: spec
    for spec in (
        StatTypeSpec(
            "base",
            "base",
            Aggregation.PER_GAME,
            Basis.OBSERVED,
            "minutes_per_game",
            "NBA.com LeagueDashPlayerStats, Base measure, PerGame mode: counting stats are "
            "per-game averages; shooting percentages are ratios in 0-1.",
        ),
        StatTypeSpec(
            "advanced",
            "advanced",
            Aggregation.PER_GAME,
            Basis.OBSERVED,
            "minutes_per_game",
            "NBA.com LeagueDashPlayerStats, Advanced measure, PerGame mode: ratings are "
            "per 100 possessions, percentages are ratios in 0-1, PACE is per 48 minutes.",
        ),
        StatTypeSpec(
            "estimated",
            "estimated",
            Aggregation.PER_GAME,
            Basis.ESTIMATED,
            "minutes_per_game",
            "NBA.com PlayerEstimatedMetrics: modelled ratings (E_*), not counted from games.",
        ),
        StatTypeSpec(
            "totals",
            "totals",
            Aggregation.TOTALS,
            Basis.OBSERVED,
            "minutes_total",
            "User-imported season totals CSV: raw season sums. The embedded per_game bag is "
            "derived by dividing by GP and is labelled as derived.",
        ),
        StatTypeSpec(
            "csv_totals",
            "totals",
            Aggregation.TOTALS,
            Basis.OBSERVED,
            "minutes_total",
            "Synthetic demo-league totals (demo_seed only).",
            synthetic=True,
        ),
    )
}


def known_stat_types() -> tuple[str, ...]:
    return tuple(_SPECS)


def spec_for(stat_type: str) -> StatTypeSpec:
    try:
        return _SPECS[stat_type]
    except KeyError:
        raise UnknownStatTypeError(
            f"no statistical definition for stat_type {stat_type!r}; "
            f"known: {', '.join(_SPECS)}"
        ) from None


def require_comparable(*stat_types: str) -> Aggregation:
    """The shared aggregation of ``stat_types``, or an error if they disagree.

    Call before putting figures from different stored rows into one calculation.
    """
    specs = [spec_for(s) for s in stat_types]
    if not specs:
        raise ValueError("require_comparable needs at least one stat_type")
    aggregations = {s.aggregation for s in specs}
    if len(aggregations) > 1:
        raise IncompatibleStatsError(
            "cannot combine "
            + " and ".join(f"{s.stat_type} ({s.aggregation.value})" for s in specs)
            + ": per-game averages and season totals differ by a factor of games played"
        )
    return aggregations.pop()


def numeric(value: Any) -> float | None:
    """A finite float, or ``None`` for anything absent or unusable. Never 0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def per_game_from_total(total: Any, games_played: Any) -> float | None:
    """A per-game figure from a season total, or ``None`` when it cannot be formed.

    Zero or missing games played is *not* a zero average; it is no average.
    """
    value, games = numeric(total), numeric(games_played)
    if value is None or games is None or games <= 0:
        return None
    return value / games


def minutes_per_game(minutes: Any, games_played: Any, stat_type: str) -> float | None:
    """The ``minutes`` column normalised to minutes per game, whatever the row's unit."""
    spec = spec_for(stat_type)
    value = numeric(minutes)
    if value is None:
        return None
    if spec.minutes_unit == "minutes_per_game":
        return value
    return per_game_from_total(value, games_played)
