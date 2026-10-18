"""API request/response models (Pydantic v2)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.provenance import (
    UNKNOWN_UPSTREAM,
    describe_providers,
    upstream_for,
)

__all__ = ["describe_providers", "upstream_for", "UNKNOWN_UPSTREAM"]

# One definition of the strategy vocabulary. It was previously inlined in
# `ScenarioIn` only, so `EvaluateRequest.strategy` and `GenerateRequest.strategy`
# were bare `str` and silently fell back to different weights on a typo (QA-7).
Strategy = Literal[
    "contend", "improve", "retool", "rebuild", "youth", "cap_relief", "custom"
]

# The report renderer only produces these two; anything else used to be accepted,
# recorded as the requested format, and then answered with markdown anyway.
ReportFormat = Literal["markdown", "html"]


class Provenance(BaseModel):
    # No defaults. The default *was* the bug: every caller that omitted these got
    # "NBA.com" regardless of what the row actually said.
    source_provider: str
    upstream: str
    source_retrieved_at: datetime | None = None

    @classmethod
    def of(cls, row: object) -> "Provenance":
        """Read provenance off a `ProvenanceMixin` row, describing what it actually is."""
        provider = getattr(row, "source_provider", None) or ""
        return cls(
            source_provider=provider or "unknown",
            upstream=upstream_for(provider),
            source_retrieved_at=getattr(row, "source_retrieved_at", None),
        )


class TeamOut(BaseModel):
    id: str
    nba_team_id: int
    full_name: str
    abbreviation: str
    nickname: str | None = None
    city: str
    conference: str | None = None
    division: str | None = None
    provenance: Provenance | None = None


class RosterPlayerOut(BaseModel):
    player_id: str
    nba_player_id: int
    name: str
    position: str | None = None
    jersey_number: str | None = None
    age: float | None = None
    height_inches: int | None = None
    years_experience: int | None = None
    tei: float | None = None
    #: The estimate's 10th/90th percentile band. Served beside the point because the model
    #: card excludes any use that strips it; None exactly when `tei` is None.
    tei_low: float | None = None
    tei_high: float | None = None
    archetype: str | None = None
    availability: float | None = None
    # Contract facts for the cap league year. All three are None when unknown — the
    # trade-evaluator roster cell rendered a hardcoded "salary — · years —" before R2b,
    # so importing contract data changed nothing on screen and the bug would have become
    # invisible the moment someone marked the import done.
    salary: int | None = None
    contract_years_remaining: int | None = None
    contract_type: str | None = None


class PlayerOut(BaseModel):
    id: str
    nba_player_id: int
    full_name: str
    is_active: bool
    position: str | None = None
    birth_date: date | None = None
    height_inches: int | None = None
    weight_lbs: int | None = None
    years_experience: int | None = None
    current_team: TeamOut | None = None
    provenance: Provenance | None = None


class ScenarioWeightsIn(BaseModel):
    performance: float = Field(ge=0, default=0.22)
    fit: float = Field(ge=0, default=0.18)
    contract: float = Field(ge=0, default=0.14)
    timeline: float = Field(ge=0, default=0.16)
    assets: float = Field(ge=0, default=0.15)
    risk: float = Field(ge=0, default=0.15)


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    focal_team_id: str
    strategy: Strategy = "custom"
    horizon_years: int = Field(ge=1, le=5, default=1)
    risk_tolerance: Literal["conservative", "balanced", "aggressive"] = "balanced"
    max_added_payroll: int | None = None
    willing_to_cross_tax: bool = False
    willing_to_cross_first_apron: bool = False
    willing_to_cross_second_apron: bool = False
    untouchable_player_ids: list[str] = []
    preferred_outgoing_player_ids: list[str] = []
    positional_needs: list[str] = []
    weights: ScenarioWeightsIn = ScenarioWeightsIn()


class ScenarioPatch(BaseModel):
    name: str | None = None
    strategy: Strategy | None = None
    horizon_years: int | None = None
    risk_tolerance: str | None = None
    untouchable_player_ids: list[str] | None = None
    preferred_outgoing_player_ids: list[str] | None = None
    weights: ScenarioWeightsIn | None = None


class PlayerMove(BaseModel):
    player_id: str
    from_team_id: str
    to_team_id: str


class PickMove(BaseModel):
    from_team_id: str
    to_team_id: str
    draft_year: int = Field(ge=2026, le=2033)
    round_number: int = Field(ge=1, le=2)
    protections: str | None = None
    is_hypothetical: bool = True


class ValidateRequest(BaseModel):
    team_ids: list[str] = Field(min_length=2, max_length=3)
    player_moves: list[PlayerMove] = []
    pick_moves: list[PickMove] = []

    @field_validator("player_moves")
    @classmethod
    def moves_within_teams(cls, v: list[PlayerMove], info):
        team_ids = set(info.data.get("team_ids", []))
        for move in v:
            if move.from_team_id not in team_ids or move.to_team_id not in team_ids:
                raise ValueError("player move references a team not in the trade")
            if move.from_team_id == move.to_team_id:
                raise ValueError("player cannot be traded to their own team")
        return v


class TradeIn(ValidateRequest):
    name: str = Field(min_length=1, max_length=120)
    scenario_id: str | None = None
    notes: str | None = None


class EvaluateRequest(ValidateRequest):
    scenario_id: str | None = None
    strategy: Strategy = "custom"
    weights: ScenarioWeightsIn | None = None


class ComparablesRequest(ValidateRequest):
    """A trade to find precedents for, from one team's point of view.

    `focal_team_id` is required and has no default: a comparable is a *side*, and picking
    a side for the caller would answer a question nobody asked.
    """

    focal_team_id: str
    k: int = Field(ge=1, le=25, default=5)


class MemoRequest(ValidateRequest):
    """A decision memo for a deal that has not been saved.

    `format` is a closed set here for the same reason it is on the saved-trade report: a
    format that is accepted and then answered with something else is a lie in the
    response headers. `json` returns the markdown plus its section list, for a client
    that renders its own.
    """

    name: str = Field(min_length=1, max_length=120, default="Untitled trade")
    focal_team_id: str
    scenario_id: str | None = None
    strategy: Strategy = "custom"
    format: Literal["markdown", "html", "json"] = "markdown"
    include_comparables: bool = True


class GenerateRequest(BaseModel):
    scenario_id: str | None = None
    focal_team_id: str | None = None
    strategy: Strategy = "custom"
    max_candidates: int = Field(ge=1, le=12, default=8)


class ComparisonIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scenario_id: str | None = None
    trade_ids: list[str] = Field(min_length=2, max_length=5)
