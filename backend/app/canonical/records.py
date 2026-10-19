"""Provider-independent records, and the envelope every provider call returns.

The principle (R8): **external providers supply information; Pivot owns its definitions.**
A provider adapter's whole job is to turn that provider's payload into these records and to
say, truthfully, how the call went. Nothing downstream of an adapter imports a
provider-specific schema.

Records carry the provider's own identifiers (``provider`` + ``provider_id``) and never a
Pivot id — attaching one is the identity layer's job (``app.canonical.identity``), and it can
fail or be ambiguous. A record that has not been resolved is still a perfectly good record.

``ProviderResult`` exists because ``list[dict]`` cannot tell *the provider has no such rows*
from *the provider did not answer*. Those need opposite handling: the first is data, the
second must never be written down as if it were.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from app.canonical.seasons import Season, SeasonType, StatScope

T = TypeVar("T")


# ------------------------------------------------------------------------ call outcome


class ProviderStatus(StrEnum):
    OK = "ok"
    #: The provider answered and the answer is genuinely "nothing matches".
    EMPTY = "empty"
    #: Network failure, timeout, 5xx after retries. The answer is unknown.
    UNAVAILABLE = "unavailable"
    #: No credential configured, or the credential was rejected (401).
    UNAUTHORIZED = "unauthorized"
    #: The credential is valid but its plan does not include this endpoint.
    TIER_RESTRICTED = "tier_restricted"
    RATE_LIMITED = "rate_limited"
    #: The provider answered with a shape the adapter's contract does not accept.
    SCHEMA_ERROR = "schema_error"
    NOT_CONFIGURED = "not_configured"


#: Statuses that say nothing about the world and must never be written as data.
NON_DATA_STATUSES = frozenset(
    {
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.UNAUTHORIZED,
        ProviderStatus.TIER_RESTRICTED,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.SCHEMA_ERROR,
        ProviderStatus.NOT_CONFIGURED,
    }
)


class ProviderUnavailableError(RuntimeError):
    """Raised by ``require_data`` when a result carries no usable answer."""

    def __init__(self, result: ProviderResult[Any]):
        self.result = result
        super().__init__(
            f"{result.meta.provider}:{result.meta.endpoint} -> {result.status.value}"
            + (f" ({result.error})" if result.error else "")
        )


@dataclass(frozen=True)
class CallMeta:
    provider: str
    endpoint: str
    params: dict[str, Any] = field(default_factory=dict)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    pages: int = 0
    requests: int = 0
    #: A response served from a local cache rather than the provider.
    from_cache: bool = False


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    status: ProviderStatus
    records: tuple[T, ...]
    meta: CallMeta
    error: str | None = None
    #: Non-fatal observations (a skipped malformed row, a truncated page limit).
    warnings: tuple[str, ...] = ()

    @property
    def has_answer(self) -> bool:
        """True when the provider answered — including a genuine "nothing"."""
        return self.status in (ProviderStatus.OK, ProviderStatus.EMPTY)

    def require_data(self) -> tuple[T, ...]:
        """The records, or a ``ProviderUnavailableError`` when there was no answer.

        An ``EMPTY`` result returns ``()``: that *is* an answer.
        """
        if not self.has_answer:
            raise ProviderUnavailableError(self)
        return self.records

    @classmethod
    def ok(
        cls, records: list[T] | tuple[T, ...], meta: CallMeta, warnings: tuple[str, ...] = ()
    ) -> ProviderResult[T]:
        records = tuple(records)
        status = ProviderStatus.OK if records else ProviderStatus.EMPTY
        return cls(status, records, meta, None, warnings)

    @classmethod
    def failed(cls, status: ProviderStatus, meta: CallMeta, error: str) -> ProviderResult[T]:
        if status not in NON_DATA_STATUSES:
            raise ValueError(f"{status.value} is not a failure status")
        return cls(status, (), meta, error)


#: ``contracts.source_name`` of a header written from a per-season contracts endpoint. Such a contract
#: carries the one season that was requested, so the number of seasons "on file" says nothing about
#: how long the contract really runs. Readers that would turn that count into "years remaining"
#: must treat the length as unknown.
SINGLE_SEASON_CONTRACT_SOURCE = "BALLDONTLIE /contracts/teams"


# ----------------------------------------------------------------------------- records


@dataclass(frozen=True)
class TeamRecord:
    provider: str
    provider_id: str
    abbreviation: str
    full_name: str
    city: str | None = None
    nickname: str | None = None
    conference: str | None = None
    division: str | None = None


@dataclass(frozen=True)
class PlayerRecord:
    provider: str
    provider_id: str
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    position: str | None = None
    height_inches: int | None = None
    weight_lbs: int | None = None
    jersey_number: str | None = None
    college: str | None = None
    country: str | None = None
    draft_year: int | None = None
    draft_round: int | None = None
    draft_number: int | None = None
    #: The provider's id for the team the player is listed on *now*, if it says. A
    #: current-roster hint, dated by ``observed_at`` on the enclosing call — not history.
    team_provider_id: str | None = None
    is_active: bool | None = None


@dataclass(frozen=True)
class GameRecord:
    provider: str
    provider_id: str
    season: Season
    season_type: SeasonType
    game_date: date
    home_team_provider_id: str
    away_team_provider_id: str
    home_score: int | None = None
    away_score: int | None = None
    #: The provider's own status vocabulary, preserved; "final" detection is the caller's.
    status: str | None = None


@dataclass(frozen=True)
class StandingRecord:
    provider: str
    team_provider_id: str
    scope: StatScope
    wins: int
    losses: int
    conference_rank: int | None = None
    division_rank: int | None = None
    home_record: str | None = None
    road_record: str | None = None


@dataclass(frozen=True)
class PlayerStatRecord:
    """One player's statistics over one scope, with the aggregation made explicit.

    ``values`` holds the provider's figures keyed by the provider's own column names, with
    absent values as ``None`` (never 0). ``aggregation`` is what the numbers are *per*;
    ``stat_group`` names the provider's category ("general/base", "clutch/advanced").
    """

    provider: str
    player_provider_id: str
    scope: StatScope
    stat_group: str
    aggregation: str
    values: dict[str, float | None]
    games_played: int | None = None
    team_provider_id: str | None = None
    #: ``"observed"`` or ``"estimated"`` (see ``app.canonical.stats.Basis``).
    basis: str = "observed"
    #: The biography the provider embeds in the row, for identity linking only. Never a stat.
    player: PlayerRecord | None = None


@dataclass(frozen=True)
class TeamStatRecord:
    provider: str
    team_provider_id: str
    scope: StatScope
    stat_group: str
    aggregation: str
    values: dict[str, float | None]
    basis: str = "observed"


@dataclass(frozen=True)
class ContractYearRecord:
    """One season of one player's contract, exactly as far as the provider states it.

    ``cap_hit``, ``base_salary`` and ``total_cash`` are three different quantities and are
    kept apart; Pivot does not decide which one a downstream rule should use. Guarantees,
    options and no-trade clauses are *not here*: a provider that does not supply them leaves
    them out, and nothing infers them.
    """

    provider: str
    player_provider_id: str
    team_provider_id: str | None
    season: Season
    cap_hit: int | None = None
    base_salary: int | None = None
    total_cash: int | None = None
    provider_contract_id: str | None = None
    #: The provider's spelling, for reports on rows that cannot be linked. Never used to link.
    player_name: str | None = None
    #: Embedded biography, for identity linking only (see ``PlayerStatRecord.player``).
    player: PlayerRecord | None = None


@dataclass(frozen=True)
class ContractAggregateRecord:
    provider: str
    player_provider_id: str
    team_provider_id: str | None
    start_season: Season | None
    end_season: Season | None
    contract_type: str | None = None
    contract_status: str | None = None
    contract_years: int | None = None
    total_value: int | None = None
    average_salary: int | None = None
    guaranteed_at_signing: int | None = None
    total_guaranteed: int | None = None
    signed_using: str | None = None
    free_agent_year: int | None = None
    free_agent_status: str | None = None
    #: The provider's free-form notes, preserved verbatim (options often live here).
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InjuryRecord:
    provider: str
    player_provider_id: str
    status: str
    description: str | None = None
    return_date: str | None = None
    team_provider_id: str | None = None
