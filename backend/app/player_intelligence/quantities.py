"""What the engine can say, per dimension, and — just as important — what it declines to say.

A dimension is a *kind of basketball evidence* (shooting, playmaking, ...), not a rating. Each
quantity states which of the three output kinds it supports **today**, why, and what it would take to
support more. A quantity that cannot be supported is listed as unavailable with a reason; it is never
filled with a stand-in number. There is deliberately no overall score and no common 0-100 scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modeling.contract import OutputKind

M, I, P = OutputKind.MEASURED, OutputKind.INFERRED, OutputKind.PROJECTED


@dataclass(frozen=True)
class QuantitySpec:
    id: str
    label: str
    dimension: str
    unit: str
    definition: str
    #: Kinds the engine implements for this quantity (a kind may still be withheld at serving time).
    kinds: tuple[OutputKind, ...]
    source: str
    #: Why the other kinds are not offered, in plain words.
    not_offered: dict[str, str] = field(default_factory=dict)
    #: ``true`` for rates whose denominator makes "no attempts" an undefined value rather than zero.
    undefined_without_attempts: bool = False


QUANTITIES: dict[str, QuantitySpec] = {
    spec.id: spec
    for spec in (
        QuantitySpec(
            "ts_pct", "True shooting %", "shooting", "fraction of 1",
            "points per 2 true-shooting attempts: PTS / (2 * (FGA + 0.44 * FTA)). A ratio estimator, not made/attempted",
            (M, I, P), "NBA.com season tables (base + advanced); integer counts recovered by constraint reconstruction",
            undefined_without_attempts=True,
        ),
        QuantitySpec(
            "fg3_pct", "Three-point accuracy", "shooting", "fraction of 1",
            "three-pointers made / attempted", (M, I, P),
            "NBA.com season tables (base + advanced); integer counts recovered by constraint reconstruction",
            undefined_without_attempts=True,
        ),
        QuantitySpec(
            "ft_pct", "Free-throw accuracy", "shooting", "fraction of 1",
            "free throws made / attempted", (M, I, P),
            "NBA.com season tables (base + advanced); integer counts recovered by constraint reconstruction",
            undefined_without_attempts=True,
        ),
        QuantitySpec(
            "ast_pct", "Assist percentage", "playmaking", "fraction of 1",
            "share of teammates' field goals assisted while the player is on the floor (NBA.com AST_PCT)", (M,),
            "NBA.com advanced table",
            {"inferred": "year-over-year correlation is ~0.90 among rotation players: the measurement is already close to the best estimate, "
                         "and a latent playmaking model has not been shown to add value; assists depend on role and teammates as much as skill",
             "projected": "persistence forecasts already capture most of the signal; no forecast has been validated for this quantity in R9.2"},
        ),
        QuantitySpec(
            "ast_to_ratio", "Assist-to-turnover ratio", "playmaking", "ratio",
            "assists per turnover (NBA.com AST_TO)", (M,), "NBA.com advanced table",
            {"inferred": "a ratio of two counts with unmodelled sampling error; not estimated", "projected": "not validated"},
        ),
        QuantitySpec(
            "reb_pct", "Total rebound %", "rebounding", "fraction of 1",
            "share of available rebounds secured while the player is on the floor (NBA.com REB_PCT)", (M,),
            "NBA.com advanced table",
            {"inferred": "year-over-year correlation ~0.95; strongly position- and scheme-dependent, so volume does not isolate ability",
             "projected": "not validated"},
        ),
        QuantitySpec(
            "oreb_pct", "Offensive rebound %", "rebounding", "fraction of 1",
            "share of available offensive rebounds secured (NBA.com OREB_PCT)", (M,), "NBA.com advanced table",
            {"inferred": "position- and scheme-dependent", "projected": "not validated"},
        ),
        QuantitySpec(
            "dreb_pct", "Defensive rebound %", "rebounding", "fraction of 1",
            "share of available defensive rebounds secured (NBA.com DREB_PCT)", (M,), "NBA.com advanced table",
            {"inferred": "position- and scheme-dependent", "projected": "not validated"},
        ),
        QuantitySpec(
            "usg_pct", "Usage %", "offensive_role", "fraction of 1",
            "share of team possessions ended by the player (shot, free-throw trip or turnover) while on the floor (NBA.com USG_PCT)",
            (M,), "NBA.com advanced table",
            {"inferred": "usage is a role a coach assigns, not an ability; a high-usage player is not thereby a better player",
             "projected": "not validated"},
        ),
        QuantitySpec(
            "minutes_per_game", "Minutes per game", "offensive_role", "minutes",
            "average minutes per game played (NBA.com MIN)", (M,), "NBA.com base table",
            {"inferred": "a coaching decision, not an ability", "projected": "not validated"},
        ),
        QuantitySpec(
            "games_played", "Games played", "availability", "games",
            "regular-season games the player appeared in (NBA.com GP). Season length varies (shortened 2019-20 and 2020-21 seasons)",
            (M,), "NBA.com base table",
            {"inferred": "an availability model (appearance and games-played propensity) is the recommended next step: see docs/r9-2/multidimensional-skill-coverage.md",
             "projected": "see above; injury records are current-only, so history cannot be reconstructed from them without hindsight"},
        ),
    )
}

DIMENSIONS: dict[str, dict] = {
    "shooting": {
        "label": "Shooting", "status": "measured + inferred (experimental) + projected (benchmark or experimental, per quantity)",
        "quantities": ["ts_pct", "fg3_pct", "ft_pct"],
    },
    "playmaking": {"label": "Playmaking", "status": "measured only", "quantities": ["ast_pct", "ast_to_ratio"]},
    "rebounding": {"label": "Rebounding", "status": "measured only", "quantities": ["reb_pct", "oreb_pct", "dreb_pct"]},
    "offensive_role": {"label": "Offensive role and usage", "status": "measured only (context, not skill)", "quantities": ["usg_pct", "minutes_per_game"]},
    "availability": {"label": "Availability", "status": "measured only; model recommended next", "quantities": ["games_played"]},
}

#: Dimensions the engine knows it cannot yet serve, each with the evidence-based reason.
UNAVAILABLE_DIMENSIONS: dict[str, dict] = {
    "defensive_contribution": {
        "label": "Defensive contribution",
        "reason": (
            "no validated measure exists in Pivot's data: blocks, steals and rebounds are recorded events, not defensive ability; "
            "tracking-based defended-field-goal and hustle data exist for a single ingested season, so stability and predictive "
            "value cannot be tested; player-level causal defensive impact is not identifiable from box-score or on/off data"
        ),
        "requirement": "a bounded multi-season backfill of defense/overall and hustle with append-only snapshots, then a stability and incremental-information study (docs/r9-2/goat-intelligence-assessment.md)",
    },
    "shot_profile": {
        "label": "Shot profile (zones)",
        "reason": "shooting-zone data exist for one ingested season; zone accuracy rests on very small samples (median 31 mid-range attempts)",
        "requirement": "multi-season zone history and an identity-corroborated provider mapping",
    },
    "shot_creation": {
        "label": "Shot creation / off-ball",
        "reason": "tracking and touch data exist for one season and are largely explained by usage, assists and minutes",
        "requirement": "multi-season history and a test of incremental information over existing role measures",
    },
    "overall_skill_rating": {
        "label": "Overall skill rating",
        "reason": "deliberately not offered: no defensible way to combine unlike quantities into one number, and TEI (a weighted descriptive composite) has not been shown to measure ability",
        "requirement": "none planned",
    },
}


def get_quantity(quantity: str) -> QuantitySpec:
    try:
        return QUANTITIES[quantity]
    except KeyError as exc:
        raise KeyError(f"unknown quantity {quantity!r}; known: {sorted(QUANTITIES)}") from exc
