"""Normalized Pivot schema.

Conventions
- Internal primary keys are UUID strings; external provider identifiers (NBA.com team
  and player IDs) are separate unique columns so identity reconciliation never leaks
  provider IDs into domain relationships.
- Every provider-derived table carries provenance columns (ProvenanceMixin): which
  provider, which upstream record, and when it was retrieved. `valid_from`/`valid_to`
  support snapshot semantics — rows are superseded, not silently overwritten.
- Wide statistical payloads keep their full normalized row in a JSON `stats` column in
  addition to first-class columns used by models and the API.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def uuid_pk() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProvenanceMixin(TimestampMixin):
    source_provider: Mapped[str] = mapped_column(String(50), default="nba_api")
    source_record_id: Mapped[str | None] = mapped_column(String(100))
    source_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_run_id: Mapped[str | None] = mapped_column(String(36))


# --------------------------------------------------------------------------- identity


class Team(Base, ProvenanceMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    nba_team_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    abbreviation: Mapped[str] = mapped_column(String(10), index=True)
    nickname: Mapped[str] = mapped_column(String(50))
    city: Mapped[str] = mapped_column(String(50))
    state: Mapped[str | None] = mapped_column(String(50))
    year_founded: Mapped[int | None] = mapped_column(Integer)
    conference: Mapped[str | None] = mapped_column(String(10))
    division: Mapped[str | None] = mapped_column(String(20))


class Player(Base, ProvenanceMixin):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    nba_player_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100), index=True)
    first_name: Mapped[str | None] = mapped_column(String(50))
    last_name: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    birth_date: Mapped[date | None] = mapped_column(Date)
    height_inches: Mapped[int | None] = mapped_column(Integer)
    weight_lbs: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[str | None] = mapped_column(String(20))
    years_experience: Mapped[int | None] = mapped_column(Integer)
    draft_year: Mapped[int | None] = mapped_column(Integer)
    draft_round: Mapped[int | None] = mapped_column(Integer)
    draft_number: Mapped[int | None] = mapped_column(Integer)


class PlayerTeamHistory(Base, ProvenanceMixin):
    """Observed player→team assignments over time (from roster snapshots)."""

    __tablename__ = "player_team_history"
    __table_args__ = (Index("ix_pth_player_season", "player_id", "season"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    season: Mapped[str] = mapped_column(String(10))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RosterEntry(Base, ProvenanceMixin):
    """Current roster snapshot rows (one active snapshot per season; superseded rows get valid_to)."""

    __tablename__ = "rosters"
    __table_args__ = (
        UniqueConstraint("team_id", "player_id", "season", "valid_from", name="uq_roster_entry"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    season: Mapped[str] = mapped_column(String(10), index=True)
    jersey_number: Mapped[str | None] = mapped_column(String(10))
    position: Mapped[str | None] = mapped_column(String(20))
    age: Mapped[float | None] = mapped_column(Float)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    player: Mapped[Player] = relationship(lazy="joined")
    team: Mapped[Team] = relationship(lazy="joined")


# ----------------------------------------------------------------------------- games


class Game(Base, ProvenanceMixin):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    nba_game_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    season: Mapped[str] = mapped_column(String(10), index=True)
    game_date: Mapped[date] = mapped_column(Date, index=True)
    home_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="final")


class PlayerGameStats(Base, ProvenanceMixin):
    __tablename__ = "player_game_stats"
    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_pgs"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    minutes: Mapped[float | None] = mapped_column(Float)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class PlayerSeasonStats(Base, ProvenanceMixin):
    """One player's statistics for one scope (season, season type) and one stat type.

    What a ``stat_type`` means — per-game average or season total, observed or modelled —
    is defined in ``app.canonical.stats``. In particular ``minutes`` is *minutes per game*
    for ``base``/``advanced``/``estimated`` rows and *minutes for the season* for
    ``totals`` rows; read it through ``minutes_per_game`` rather than directly.
    """

    __tablename__ = "player_season_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "season_type", "stat_type", name="uq_pss_scope"),
        Index("ix_pss_season", "season", "stat_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    season: Mapped[str] = mapped_column(String(10))
    #: regular | playoffs | play_in | preseason | ist (`app.canonical.seasons.SeasonType`).
    #: Every row ingested before R8 is a regular-season row (NBA.com's default, and the
    #: imported CSV's games-played matches the regular-season base row for all 573 players).
    season_type: Mapped[str] = mapped_column(String(12), default="regular", server_default="regular")
    stat_type: Mapped[str] = mapped_column(String(20))  # base | advanced | estimated | totals
    games_played: Mapped[int | None] = mapped_column(Integer)
    minutes: Mapped[float | None] = mapped_column(Float)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)

    player: Mapped[Player] = relationship(lazy="joined")


class TeamSeasonStats(Base, ProvenanceMixin):
    __tablename__ = "team_season_stats"
    __table_args__ = (
        UniqueConstraint("team_id", "season", "season_type", "stat_type", name="uq_tss_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    season: Mapped[str] = mapped_column(String(10), index=True)
    season_type: Mapped[str] = mapped_column(String(12), default="regular", server_default="regular")
    stat_type: Mapped[str] = mapped_column(String(20))
    stats: Mapped[dict] = mapped_column(JSON, default=dict)

    team: Mapped[Team] = relationship(lazy="joined")


class SeasonCalendar(Base, ProvenanceMixin):
    """The dates a season was actually played, one row per season.

    It exists because a trade has to be described by production that existed when it was
    made, and the month a trade falls in does not settle that. Three cases in the ten
    ingested seasons decide it and none of them is a corner case:

    - **The 2020-21 season began 22 December 2020.** Thirty-three trades were made in
      November 2020 — draft night and the free-agency window that followed. Reading the
      month alone calls those in-season and describes them by 2020-21 production that had
      not been played yet.
    - **Draft-night trades sit on the upcoming season's page.** Basketball-Reference lists
      the 2024 draft under 2024-25, so twelve June-2024 trades carry that season label
      while the season they should be described by is 2023-24.
    - **Early-October trades are preseason.** Ten of them across the corpus, the earliest
      on 1 October, against first games that fall between 22 October and 25 October.

    So the season boundary is ingested rather than assumed: `first_game_date` and
    `last_game_date` come from `LeagueGameLog`, the same provider every other season fact
    here comes from, and `services/comparables.feature_season_for` reads them.
    """

    __tablename__ = "season_calendar"
    __table_args__ = (UniqueConstraint("season", name="uq_season_calendar"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    season: Mapped[str] = mapped_column(String(10), index=True, unique=True)
    #: First and last REGULAR-SEASON game. The play-in and the playoffs are deliberately
    #: outside the window: a trade cannot be made during them, so they would only widen
    #: the in-season band without ever changing an answer.
    first_game_date: Mapped[date] = mapped_column(Date)
    last_game_date: Mapped[date] = mapped_column(Date)
    game_count: Mapped[int] = mapped_column(Integer)


class Standing(Base, ProvenanceMixin):
    __tablename__ = "standings"
    __table_args__ = (UniqueConstraint("team_id", "season", name="uq_standing"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    season: Mapped[str] = mapped_column(String(10), index=True)
    wins: Mapped[int] = mapped_column(Integer)
    losses: Mapped[int] = mapped_column(Integer)
    win_pct: Mapped[float] = mapped_column(Float)
    conference: Mapped[str | None] = mapped_column(String(10))
    conference_rank: Mapped[int | None] = mapped_column(Integer)
    playoff_rank: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    team: Mapped[Team] = relationship(lazy="joined")


# ------------------------------------------------------------------- contracts / caps


class Contract(Base, ProvenanceMixin):
    """Contract header from an optional non-nba_api provider. Absent unless configured."""

    __tablename__ = "contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    # Nullable: NULL means the provider does not report contract type. A default of
    # "standard" asserted that every contract was standard, which flipped ROSTER_SIZE
    # from (warning, medium) to (pass, high) on data nobody had (C9).
    contract_type: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)
    signed_date: Mapped[date | None] = mapped_column(Date)
    no_trade_clause: Mapped[bool | None] = mapped_column(Boolean)
    source_name: Mapped[str] = mapped_column(String(100))
    source_date: Mapped[date | None] = mapped_column(Date)

    years: Mapped[list["ContractYear"]] = relationship(back_populates="contract", lazy="selectin")


class ContractYear(Base, ProvenanceMixin):
    __tablename__ = "contract_years"
    __table_args__ = (UniqueConstraint("contract_id", "season", name="uq_contract_year"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    season: Mapped[str] = mapped_column(String(10), index=True)
    salary: Mapped[int] = mapped_column(Integer)
    guaranteed: Mapped[int | None] = mapped_column(Integer)
    player_option: Mapped[bool | None] = mapped_column(Boolean)
    team_option: Mapped[bool | None] = mapped_column(Boolean)
    #: R8.3. ``salary`` is the figure every reader uses; ``salary_basis`` names what it is
    #: ("cap_hit" for BALLDONTLIE rows, NULL for earlier snapshots whose basis was never stated).
    #: ``base_salary`` and ``total_cash`` are different quantities and are kept, not merged.
    base_salary: Mapped[int | None] = mapped_column(Integer)
    total_cash: Mapped[int | None] = mapped_column(Integer)
    salary_basis: Mapped[str | None] = mapped_column(String(20))

    contract: Mapped[Contract] = relationship(back_populates="years")


class Injury(Base, ProvenanceMixin):
    """Populated only when an injury provider is configured. Never fabricated."""

    __tablename__ = "injuries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(Text)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The provider's expected return date, if it gives one. Absent is NULL, never "healthy".
    return_date: Mapped[date | None] = mapped_column(Date)


class Transaction(Base, ProvenanceMixin):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    transaction_type: Mapped[str] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(Text)
    occurred_on: Mapped[date | None] = mapped_column(Date)


class HistoricalTrade(Base, ProvenanceMixin):
    """A completed NBA trade, as a secondary provider reported it.

    Separate from `TradeProposal` on purpose: a proposal is something a user built and
    this is something that happened. They share no lifecycle, no editability and no
    identity, and folding the two into one table would make "did this trade occur?" a
    column rather than a table.

    `n_teams` is the source's own count, cross-checked against the distinct teams the
    legs name. The two agree on all 565 trades in the ten-season corpus, and the importer
    files a data-quality warning where they would not.
    """

    __tablename__ = "historical_trades"
    __table_args__ = (
        UniqueConstraint("source_provider", "source_record_id", name="uq_historical_trade"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    season: Mapped[str] = mapped_column(String(10), index=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    n_teams: Mapped[int] = mapped_column(Integer)
    #: The source's sentence, flattened to text and kept verbatim. Everything derived
    #: below can be checked against it by a person.
    source_text: Mapped[str] = mapped_column(Text)
    #: The source's trailing notes — pick conditions and trade-exception sentences — kept
    #: verbatim for the same reason.
    notes_text: Mapped[str | None] = mapped_column(Text)
    #: Asset phrases the grammar did not recognize. Kept rather than dropped: a trade with
    #: an unreadable leg is still a real trade, and pretending the leg was empty is how a
    #: retrieval engine ends up comparing against a deal that never happened.
    unparsed_assets: Mapped[list] = mapped_column(JSON, default=list)
    #: Teams the notes say received a trade exception, resolved against this trade's own
    #: participants. A city naming two participants (Los Angeles) resolves to neither.
    trade_exception_team_ids: Mapped[list] = mapped_column(JSON, default=list)
    trade_exception_unresolved: Mapped[list] = mapped_column(JSON, default=list)

    assets: Mapped[list["HistoricalTradeAsset"]] = relationship(
        back_populates="trade", lazy="selectin", cascade="all, delete-orphan"
    )


class HistoricalTradeAsset(Base, TimestampMixin):
    """One asset moving one way in a historical trade.

    Direction is stored per asset rather than per team, because a three-team trade has
    legs in both directions between the same pair and a per-team representation cannot
    express that. `from_team_id` / `to_team_id` are nullable only so an unresolvable
    franchise abbreviation is recorded as unresolved instead of silently dropped; the
    abbreviations the source printed are always kept.
    """

    __tablename__ = "historical_trade_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trade_id: Mapped[str] = mapped_column(ForeignKey("historical_trades.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(20))  # player | pick | cash
    from_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    to_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    from_abbreviation: Mapped[str] = mapped_column(String(10))
    to_abbreviation: Mapped[str] = mapped_column(String(10))

    # --- player legs ---
    player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"))
    #: The name the source printed. Retained even when resolution succeeds, so a wrong
    #: match is visible rather than hidden behind this database's spelling.
    player_name: Mapped[str | None] = mapped_column(String(120))
    #: Basketball-Reference's own player id. Not a Pivot identity — kept so a
    #: resolution can be re-checked against the source rather than only against a name.
    source_player_slug: Mapped[str | None] = mapped_column(String(20))
    #: How the name became a player here: nba_player_id | exact_name | unaccented |
    #: suffix_insensitive | *+season | ambiguous | none. Never blank on a player leg.
    resolution_method: Mapped[str | None] = mapped_column(String(30))
    via_draft_rights: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- pick legs ---
    draft_year: Mapped[int | None] = mapped_column(Integer)
    round_number: Mapped[int | None] = mapped_column(Integer)
    #: unconditional | protected | swap | conditional — the same vocabulary
    #: `draft_picks.conveyance` uses.
    conveyance: Mapped[str | None] = mapped_column(String(20))
    note_text: Mapped[str | None] = mapped_column(Text)
    #: True when the trade moved more than one pick with this year and round, so the
    #: source's note could not be bound to exactly one of them. The note is attached to
    #: both rather than assigned to one by guessing.
    note_binding_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Who the pick became, where the source says so. Never a traded player.
    later_selected: Mapped[str | None] = mapped_column(String(120))

    trade: Mapped[HistoricalTrade] = relationship(back_populates="assets")
    player: Mapped[Player | None] = relationship(lazy="joined")


class DraftPick(Base, ProvenanceMixin):
    """Verified pick ownership requires an authoritative provider; hypothetical picks
    used in trade construction live in trade_assets with is_hypothetical=True."""

    __tablename__ = "draft_picks"
    __table_args__ = (Index("ix_draft_picks_year_round", "draft_year", "round_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    original_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    owning_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    draft_year: Mapped[int] = mapped_column(Integer)
    round_number: Mapped[int] = mapped_column(Integer)
    protections: Mapped[str | None] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    #: How the pick conveys: unconditional | protected | swap | conditional | unparsed.
    #: NULL means the row predates the distinction, not that it is unconditional (R5-2).
    conveyance: Mapped[str | None] = mapped_column(String(20))
    #: The source's own sentence, kept verbatim. A swap clause that this codebase cannot
    #: reduce to a rule is still readable by a person, and paraphrasing it would lose the
    #: only authoritative statement of the terms.
    source_text: Mapped[str | None] = mapped_column(Text)


class LeagueCapParameters(Base, TimestampMixin):
    """Official league-year cap figures loaded from version-controlled YAML."""

    __tablename__ = "league_cap_parameters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    league_year: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    salary_cap: Mapped[int] = mapped_column(Integer)
    luxury_tax: Mapped[int] = mapped_column(Integer)
    first_apron: Mapped[int] = mapped_column(Integer)
    second_apron: Mapped[int] = mapped_column(Integer)
    minimum_team_salary: Mapped[int] = mapped_column(Integer)
    effective_date: Mapped[date | None] = mapped_column(Date)
    source_name: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(500))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    extras: Mapped[dict] = mapped_column(JSON, default=dict)


# ------------------------------------------------------------------ data engineering


class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    upstream: Mapped[str] = mapped_column(String(100))
    package_version: Mapped[str | None] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)


class DataSyncRun(Base, TimestampMixin):
    __tablename__ = "data_sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    job_name: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    error_class: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class DataQualityIssue(Base, TimestampMixin):
    __tablename__ = "data_quality_issues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    check_name: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    entity: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ----------------------------------------------------------------------------- models


def _default_model_status(context) -> str:  # type: ignore[no-untyped-def]
    """A row created with ``is_active=True`` is ``active``; anything else is ``retired`` unless the
    caller says otherwise (``candidate``). Keeps every pre-R9.1 writer correct without editing it."""
    return "active" if context.get_current_parameters().get("is_active") else "retired"


class ModelVersion(Base, TimestampMixin):
    __tablename__ = "model_versions"
    # A version string must identify a model. Before R1-9 it was a training-run
    # timestamp, so all three models trained in one run shared `v202607210204` and the
    # table held it three times per model. Versions are content hashes now, and the
    # constraint keeps them that way.
    __table_args__ = (UniqueConstraint("model_name", "version", name="uq_model_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    model_name: Mapped[str] = mapped_column(String(50), index=True)
    version: Mapped[str] = mapped_column(String(30))
    #: 200: the real labels run to 97 characters (SQLite never enforced the old 50; PostgreSQL does).
    algorithm: Mapped[str] = mapped_column(String(200))
    training_period: Mapped[str | None] = mapped_column(String(50))
    feature_list: Mapped[list] = mapped_column(JSON, default=list)
    target: Mapped[str | None] = mapped_column(String(100))
    validation_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(String(300))
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code_commit: Mapped[str | None] = mapped_column(String(50))
    data_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    #: R9.1. ``active`` (exactly the rows with ``is_active``) | ``retired`` (was active, superseded) |
    #: ``candidate`` (trained for evaluation; never read by any serving path and never touched by the
    #: legacy ``train``/``score`` garbage collector) | ``rejected`` (a candidate a reviewer declined).
    #: Promotion is a separate, audited step that R9.1 deliberately does not implement.
    status: Mapped[str] = mapped_column(
        String(12), default=_default_model_status, server_default="retired"
    )
    #: R9.1. The full, JSON-serialisable specification that produced this model (candidate spec,
    #: target, hyper-parameters, eligibility rules). Enough, with ``lineage``, to retrain it.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    #: R9.1. Where the model came from: data-snapshot fingerprint, random seed, dependency versions,
    #: code commit and the experiment that evaluated it.
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)


class PlayerImpactEstimate(Base, TimestampMixin):
    __tablename__ = "player_impact_estimates"
    __table_args__ = (UniqueConstraint("player_id", "season", "model_version_id", name="uq_pie"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    season: Mapped[str] = mapped_column(String(10), index=True)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"))
    #: Pivot Estimated Impact — a unitless index (weighted z-score composite × 2.5), not
    #: points per 100 possessions. The acronym predates the renames from
    #: TradeLab and is kept because it names this column and every API field built on
    #: it; see `analytics/impact.py`.
    tei: Mapped[float] = mapped_column(Float)
    tei_offense: Mapped[float | None] = mapped_column(Float)
    tei_defense: Mapped[float | None] = mapped_column(Float)
    tei_low: Mapped[float | None] = mapped_column(Float)  # analytic 10th percentile band
    tei_high: Mapped[float | None] = mapped_column(Float)  # analytic 90th percentile band
    availability: Mapped[float | None] = mapped_column(Float)  # 0..1 weighted GP share
    minutes_estimate: Mapped[float | None] = mapped_column(Float)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)

    player: Mapped[Player] = relationship(lazy="joined")


class CandidateEstimate(Base, TimestampMixin):
    """One estimate produced by a *candidate* model (R9.1).

    Kept apart from ``player_impact_estimates`` on purpose. The serving tables are read by
    every tool and counted by the data-health pages; a candidate row placed there would be one
    unfiltered query away from reaching a user. Here it cannot be: nothing in the serving path
    reads this table, so a candidate is isolated by construction rather than by every future
    reader remembering a filter.

    The row says what kind of quantity it is (``output_kind``: ``projected`` for a forecast of a
    future season, ``inferred`` for an estimate of underlying ability), which statistical season
    it was computed from, which season it concerns, and carries its interval and sample size.
    It is generic over ``target`` so R9.2's skill estimates use the same table.
    """

    __tablename__ = "candidate_estimates"
    __table_args__ = (
        UniqueConstraint(
            "model_version_id", "player_id", "season", "target", name="uq_candidate_estimate"
        ),
        Index("ix_candidate_estimates_version", "model_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_versions.id"))
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    #: The statistical season the estimate is computed *from* (the information cutoff).
    season: Mapped[str] = mapped_column(String(10))
    #: The season the quantity is about (``season`` itself for inferred ability, the next season
    #: for a projection).
    target_season: Mapped[str] = mapped_column(String(10))
    target: Mapped[str] = mapped_column(String(40))
    output_kind: Mapped[str] = mapped_column(String(10))  # measured | inferred | projected
    estimate: Mapped[float] = mapped_column(Float)
    interval_low: Mapped[float | None] = mapped_column(Float)
    interval_high: Mapped[float | None] = mapped_column(Float)
    #: Nominal level of the interval (e.g. 0.8). Nominal, not measured: the measured coverage of
    #: the method is in the candidate's ``validation_metrics``.
    interval_level: Mapped[float | None] = mapped_column(Float)
    exposure: Mapped[float | None] = mapped_column(Float)  # minutes (or attempts) behind the estimate
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)


class PlayerArchetype(Base, TimestampMixin):
    __tablename__ = "player_archetypes"
    __table_args__ = (UniqueConstraint("player_id", "season", name="uq_archetype"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    season: Mapped[str] = mapped_column(String(10))
    # `role_id` comes from `archetypes.ROLE_ID`, a frozen append-only map, so the number
    # is stable across retrains. It replaced a k-means `cluster_id`, which was an
    # arbitrary index that could change meaning on every run (R4-3).
    role_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(50))
    # The values that determined the branch — "why this role" — replacing a centroid
    # distance that a rule chain does not have.
    role_inputs: Mapped[dict] = mapped_column(JSON, default=dict)


class TeamNeed(Base, TimestampMixin):
    __tablename__ = "team_needs"
    __table_args__ = (UniqueConstraint("team_id", "season", "need_key", name="uq_team_need"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    season: Mapped[str] = mapped_column(String(10))
    need_key: Mapped[str] = mapped_column(String(50))
    severity: Mapped[float] = mapped_column(Float)  # 0..1
    percentile: Mapped[float | None] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)


# -------------------------------------------------------------------------- scenarios


class Scenario(Base, TimestampMixin):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(120))
    focal_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    strategy: Mapped[str] = mapped_column(String(60))
    horizon_years: Mapped[int] = mapped_column(Integer, default=1)
    risk_tolerance: Mapped[str] = mapped_column(String(20), default="balanced")
    max_added_payroll: Mapped[int | None] = mapped_column(Integer)
    willing_to_cross_tax: Mapped[bool] = mapped_column(Boolean, default=False)
    willing_to_cross_first_apron: Mapped[bool] = mapped_column(Boolean, default=False)
    willing_to_cross_second_apron: Mapped[bool] = mapped_column(Boolean, default=False)
    untouchable_player_ids: Mapped[list] = mapped_column(JSON, default=list)
    preferred_outgoing_player_ids: Mapped[list] = mapped_column(JSON, default=list)
    positional_needs: Mapped[list] = mapped_column(JSON, default=list)
    min_retained_first_round_picks: Mapped[int | None] = mapped_column(Integer)

    focal_team: Mapped[Team] = relationship(lazy="joined")
    weights: Mapped[list["ScenarioWeight"]] = relationship(
        back_populates="scenario", lazy="selectin", cascade="all, delete-orphan"
    )


class ScenarioWeight(Base, TimestampMixin):
    __tablename__ = "scenario_weights"
    __table_args__ = (UniqueConstraint("scenario_id", "component", name="uq_scenario_weight"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), index=True)
    component: Mapped[str] = mapped_column(
        String(30)
    )  # performance|fit|contract|timeline|assets|risk
    weight: Mapped[float] = mapped_column(Float)

    scenario: Mapped[Scenario] = relationship(back_populates="weights")


# ----------------------------------------------------------------------------- trades


class TradeProposal(Base, TimestampMixin):
    __tablename__ = "trade_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    notes: Mapped[str | None] = mapped_column(Text)

    teams: Mapped[list["TradeTeam"]] = relationship(
        back_populates="trade", lazy="selectin", cascade="all, delete-orphan"
    )
    assets: Mapped[list["TradeAsset"]] = relationship(
        back_populates="trade", lazy="selectin", cascade="all, delete-orphan"
    )


class TradeTeam(Base, TimestampMixin):
    __tablename__ = "trade_teams"
    __table_args__ = (UniqueConstraint("trade_id", "team_id", name="uq_trade_team"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trade_proposals.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))

    trade: Mapped[TradeProposal] = relationship(back_populates="teams")
    team: Mapped[Team] = relationship(lazy="joined")


class TradeAsset(Base, TimestampMixin):
    __tablename__ = "trade_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trade_proposals.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String(20))  # player | pick
    from_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    to_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"))
    draft_year: Mapped[int | None] = mapped_column(Integer)
    round_number: Mapped[int | None] = mapped_column(Integer)
    protections: Mapped[str | None] = mapped_column(String(200))
    is_hypothetical: Mapped[bool] = mapped_column(Boolean, default=False)

    trade: Mapped[TradeProposal] = relationship(back_populates="assets")
    player: Mapped[Player | None] = relationship(lazy="joined")


class TradeRuleResult(Base, TimestampMixin):
    __tablename__ = "trade_rule_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trade_proposals.id"), index=True)
    rule_code: Mapped[str] = mapped_column(String(60))
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    status: Mapped[str] = mapped_column(String(20))  # pass|fail|warning|unavailable
    message: Mapped[str] = mapped_column(Text)
    calculation: Mapped[dict] = mapped_column(JSON, default=dict)
    source_reference: Mapped[str | None] = mapped_column(String(300))
    confidence: Mapped[str] = mapped_column(String(10), default="high")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TradeEvaluation(Base, TimestampMixin):
    __tablename__ = "trade_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trade_proposals.id"), index=True)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    legality_status: Mapped[str] = mapped_column(String(30))
    composite_utility: Mapped[float | None] = mapped_column(Float)
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    uncertainty: Mapped[dict] = mapped_column(JSON, default=dict)
    sensitivity: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    data_version: Mapped[str | None] = mapped_column(String(30))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ComparisonSet(Base, TimestampMixin):
    __tablename__ = "comparison_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"))
    name: Mapped[str] = mapped_column(String(120))
    trade_ids: Mapped[list] = mapped_column(JSON, default=list)


class GeneratedReport(Base, TimestampMixin):
    __tablename__ = "generated_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trade_proposals.id"), index=True)
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"))
    format: Mapped[str] = mapped_column(String(10), default="markdown")
    content: Mapped[str] = mapped_column(Text)
    llm_enhanced: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MediaAsset(Base, ProvenanceMixin):
    """Asset manifest for user-supplied local images (player photos, team logos).

    Files stay outside git and outside the frontend bundle; the backend serves them
    by stable NBA id with deterministic fallbacks. `match_method`/`confidence`
    record how a name-keyed file was resolved to an identity — unmatched files are
    kept for review, never guessed into a player."""

    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("entity_type", "file_path", name="uq_media_asset_path"),
        Index("ix_media_entity", "entity_type", "nba_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    entity_type: Mapped[str] = mapped_column(String(20))  # player | team
    player_id: Mapped[str | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"))
    nba_id: Mapped[int | None] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(String(500))  # relative to the asset root dir
    content_type: Mapped[str] = mapped_column(String(50), default="image/jpeg")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    match_method: Mapped[str] = mapped_column(
        String(30)
    )  # nba_id|exact_name|normalized_name|abbreviation|manual
    confidence: Mapped[str] = mapped_column(String(20), default="high")  # high|medium|unmatched
    source_label: Mapped[str] = mapped_column(String(100))  # e.g. folder name the file came from
    alt_text: Mapped[str] = mapped_column(String(200))
    #: Whether Pivot may show this file, and where. `unverified` is the honest default for
    #: anything whose licence nobody has checked; it is never inferred from a file being
    #: publicly reachable. Values: unverified | dev_only | licensed | restricted.
    usage_status: Mapped[str] = mapped_column(
        String(20), default="unverified", server_default="unverified"
    )
    #: A pointer to the licence/permission record (contract id, Commons file page, ...).
    license_ref: Mapped[str | None] = mapped_column(String(300))
    #: Where the file originally came from, when known.
    source_url: Mapped[str | None] = mapped_column(String(500))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)


# ---------------------------------------------------------- provider identity crosswalk


class ProviderEntityId(Base, TimestampMixin):
    """Maps a provider's identifier for a player/team to Pivot's own internal id.

    ``players.nba_player_id`` and ``teams.nba_team_id`` remain the NBA.com keys and need no
    row here. This table exists for every *other* provider: BALLDONTLIE's player id 237
    is not NBA.com's 2544, and nothing in BALLDONTLIE's payload says which is which. Each
    link records how it was made (``match_method``), how sure that was, and the evidence, so
    a wrong link is findable and a new link never silently overwrites an old one.
    """

    __tablename__ = "provider_entity_ids"
    __table_args__ = (
        UniqueConstraint("entity_type", "provider", "provider_id", name="uq_provider_entity"),
        Index("ix_provider_entity_internal", "entity_type", "internal_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    entity_type: Mapped[str] = mapped_column(String(10))  # player | team
    provider: Mapped[str] = mapped_column(String(50))
    provider_id: Mapped[str] = mapped_column(String(100))
    #: `players.id` / `teams.id` (the UUID), not the NBA.com integer.
    internal_id: Mapped[str] = mapped_column(String(36))
    match_method: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(10), default="high")  # high|medium|manual
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ----------------------------------------------------------- provider statistics (R8.3)


class ProviderStatSnapshot(Base, TimestampMixin):
    """One provider's statistics for one player or team over one scope, in its own vocabulary.

    Deliberately separate from ``player_season_stats``/``team_season_stats``, whose ``stat_type``
    vocabulary is NBA.com's and whose unique key has no provider: a BALLDONTLIE row written there
    would collide with, or silently replace, an NBA.com row. ``values`` keeps the provider's field
    names; missing figures are JSON null, never 0; provider-relative ``*_rank`` fields are dropped.
    ``aggregation`` says what the figures are per (``per_game`` was verified against NBA.com for
    ``general/base``; ``unspecified`` where it has not been).
    """

    __tablename__ = "provider_stat_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider", "entity_type", "internal_id", "season", "season_type", "stat_group",
            name="uq_provider_stat_scope",
        ),
        Index(
            "ix_pss_provider_scope", "provider", "entity_type", "season", "season_type", "stat_group"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    provider: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(10))  # player | team
    #: `players.id` / `teams.id` (the UUID), as in ``provider_entity_ids``.
    internal_id: Mapped[str] = mapped_column(String(36))
    season: Mapped[str] = mapped_column(String(10))
    season_type: Mapped[str] = mapped_column(String(12))
    stat_group: Mapped[str] = mapped_column(String(40))  # "general/advanced", "hustle"
    aggregation: Mapped[str] = mapped_column(String(20))
    basis: Mapped[str] = mapped_column(String(12), default="observed", server_default="observed")
    games_played: Mapped[int | None] = mapped_column(Integer)
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    source_record_id: Mapped[str | None] = mapped_column(String(100))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ingestion_run_id: Mapped[str | None] = mapped_column(String(36))

    # The canonical provenance summariser (`build_source_meta`) reads these two names.
    @property
    def source_provider(self) -> str:
        return self.provider

    @property
    def source_retrieved_at(self) -> datetime:
        return self.retrieved_at
