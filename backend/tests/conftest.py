"""Shared test fixtures.

All NBA-like records created here are SYNTHETIC TEST FIXTURES for deterministic
tests only — they are never loaded by production configuration (see §2.11 of the
product spec and data/README.md)."""

import os
import tempfile
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# No test may reach Basketball-Reference or NBA.com. `test_every_documented_command_is_
# reachable` runs every CLI command, and two of them fetch from a third party; this makes
# them refuse instead.
os.environ.setdefault("ROSTERLAB_OFFLINE", "1")

from app.db.models import (  # noqa: E402
    Base,
    Contract,
    ContractYear,
    LeagueCapParameters,
    ModelVersion,
    Player,
    PlayerImpactEstimate,
    PlayerSeasonStats,
    RosterEntry,
    Team,
    TeamNeed,
)

#: LangChain's own environment switches. Ask Pivot ignores them in the app (its tracing needs
#: ASSISTANT_TRACING), but tests that invoke the chain directly run outside its tracing scope,
#: where an exported LANGSMITH_TRACING=true plus a key would upload test runs.
_LANGCHAIN_TRACING_ENV = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGCHAIN_PROJECT",
)


@pytest.fixture(autouse=True)
def _no_langchain_tracing_in_assistant_tests(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if "assistant" in request.node.nodeid:
        for name in _LANGCHAIN_TRACING_ENV:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection: TestClient threads see the schema
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    session = TestSession()
    yield session
    session.close()


@pytest.fixture()
def cap_params(db: Session) -> LeagueCapParameters:
    params = LeagueCapParameters(
        league_year="2026-27",
        salary_cap=164_961_000,
        luxury_tax=200_428_000,
        first_apron=209_015_000,
        second_apron=221_686_000,
        minimum_team_salary=148_465_000,
        source_name="test fixture (values match official 2026-27 release)",
    )
    db.add(params)
    db.commit()
    return params


def make_team(db: Session, nba_id: int, abbr: str, name: str | None = None) -> Team:
    team = Team(
        nba_team_id=nba_id,
        full_name=name or f"Test {abbr}",
        abbreviation=abbr,
        nickname=abbr,
        city="Testville",
    )
    db.add(team)
    db.commit()
    return team


def make_player(
    db: Session,
    nba_id: int,
    name: str,
    team: Team | None = None,
    season: str = "2025-26",
    salary: int | None = None,
    league_year: str = "2026-27",
    contract_type: str | None = "standard",
    signed_date: date | None = None,
    no_trade_clause: bool | None = None,
    birth_date: date | None = date(1998, 1, 1),
) -> Player:
    player = Player(
        nba_player_id=nba_id,
        full_name=name,
        is_active=True,
        birth_date=birth_date,
        height_inches=78,
        position="F",
    )
    db.add(player)
    db.flush()
    if team is not None:
        db.add(
            RosterEntry(
                team_id=team.id,
                player_id=player.id,
                season=season,
                is_current=True,
                age=27.0,
                source_retrieved_at=datetime.now(UTC),
            )
        )
    if salary is not None:
        contract = Contract(
            player_id=player.id,
            contract_type=contract_type,
            signed_date=signed_date,
            no_trade_clause=no_trade_clause,
            source_name="test fixture",
            source_date=date(2026, 7, 1),
        )
        db.add(contract)
        db.flush()
        db.add(ContractYear(contract_id=contract.id, season=league_year, salary=salary))
    db.commit()
    return player


@pytest.fixture()
def two_teams(db: Session) -> tuple[Team, Team]:
    return make_team(db, 1, "AAA"), make_team(db, 2, "BBB")


@pytest.fixture()
def tmp_sqlite_url() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield f"sqlite:///{path}"
    os.unlink(path)


# --------------------------------------------------------------------- seeded_league
#
# The `seeded` fixture in tests/integration/test_api.py has rosters but no
# PlayerImpactEstimate rows, so every evaluation card falls back to tei = 0.0 and no
# sanity property about impact is testable against it. `seeded_league` is the
# modelling-path fixture: two 15-player rosters with impact estimates, season stats
# rich enough for the skill vectors to vary, team needs, and active model versions.


def _season_stat_rows(
    db: Session,
    player: Player,
    index: int,
    season: str,
    retrieved_at: datetime,
    team_id: str | None = None,
) -> None:
    """Deterministic, index-varied base/advanced rows so percentile skills are not constant.

    `team_id` is set because R4's defensive differential is measured against a player's
    *teammates*; without it every fixture player lands in one nameless roster and the
    differential collapses toward a league-wide mean.
    """
    minutes = 8.0 + (index % 12) * 2.4  # 8.0 .. 34.4 mpg
    games = 55 + (index % 7) * 4  # 55 .. 79
    possessions = games * minutes * 2.1
    attempts_3 = 0.5 + (index % 9) * 0.95
    db.add(
        PlayerSeasonStats(
            player_id=player.id,
            season=season,
            stat_type="base",
            games_played=games,
            minutes=minutes,
            team_id=team_id,
            stats={
                "PTS": 4.0 + (index % 11) * 2.3,
                "REB": 2.0 + (index % 9) * 0.9,
                "AST": 1.0 + (index % 8) * 0.85,
                "STL": 0.3 + (index % 6) * 0.22,
                "BLK": 0.1 + (index % 5) * 0.31,
                "TOV": 0.6 + (index % 7) * 0.24,
                # R4: fouls feed the defensive composite, three-point makes feed shrunk
                # accuracy. Varied on a different modulus from attempts so the resulting
                # percentage is not a step function of the index.
                "PF": 0.9 + (index % 8) * 0.34,
                "FGA": 4.0 + (index % 10) * 1.7,
                "FG3A": attempts_3,
                "FG3M": attempts_3 * (0.28 + (index % 13) * 0.011),
                "FTA": 0.8 + (index % 6) * 0.7,
                "PLUS_MINUS": -4.0 + (index % 13) * 0.8,
                "AGE": 21.0 + (index % 14),
                "POSS": possessions,
            },
            source_retrieved_at=retrieved_at,
        )
    )
    db.add(
        PlayerSeasonStats(
            player_id=player.id,
            season=season,
            stat_type="advanced",
            games_played=games,
            minutes=minutes,
            stats={
                "OFF_RATING": 108.0 + (index % 11) * 1.4,
                "DEF_RATING": 106.0 + (index % 9) * 1.6,
                "NET_RATING": -6.0 + (index % 13) * 1.1,
                "AST_PCT": 0.06 + (index % 12) * 0.026,
                "AST_TO": 0.8 + (index % 8) * 0.25,
                "OREB_PCT": 0.01 + (index % 7) * 0.017,
                "DREB_PCT": 0.06 + (index % 10) * 0.019,
                "REB_PCT": 0.04 + (index % 9) * 0.017,
                "TM_TOV_PCT": 0.07 + (index % 11) * 0.011,
                "EFG_PCT": 0.47 + (index % 8) * 0.012,
                "TS_PCT": 0.50 + (index % 9) * 0.013,
                "USG_PCT": 0.11 + (index % 12) * 0.017,
                "PACE": 97.0 + (index % 5) * 1.2,
                "PIE": 0.04 + (index % 11) * 0.012,
                "POSS": possessions,
            },
            source_retrieved_at=retrieved_at,
        )
    )


@pytest.fixture()
def seeded_league(db: Session, cap_params: LeagueCapParameters) -> dict:
    """Two 15-man rosters on the full modelling path.

    Three deliberate holes, each pinning a distinct honesty property:

    - AAA[14] (`unmodeled`) has **no PlayerImpactEstimate** — the fixture for
      "a rendered value must never derive from an undisclosed default".
    - AAA[13] and BBB[13] (`no_contract_a` / `no_contract_b`) have **no Contract**, so
      the contract-value component is excluded and the weights renormalize. This mirrors
      the live database (`contracts` = 0 rows), which is the state every audit
      reproduction was run against.
    - Impact bands are the production width (6.3106 index points, the single distinct
      value observed live) so that `TEI_SIGMA_DEFAULT = 1.5` is *narrower* than a real
      player's band, as it is in production.
    """
    from app.core.cache import get_cache

    get_cache().bump_data_version()  # skills are cached per data version; isolate this fixture

    now = datetime.now(UTC)
    impact_version = ModelVersion(
        model_name="player_impact",
        version="test-impact-v1",
        algorithm="test fixture",
        training_period="2023-24..2025-26",
        target="test fixture target",
        validation_metrics={"note": "synthetic test fixture"},
        trained_at=now,
        is_active=True,
    )
    projection_version = ModelVersion(
        model_name="team_projection",
        version="test-projection-v1",
        algorithm="ols",
        validation_metrics={
            "slope": 2.235,
            "intercept": 41.0,
            "r2": 0.9527,
            "residual_std": 2.894,
            "n": 90,
            "calibrated": True,
        },
        trained_at=now,
        is_active=True,
    )
    db.add_all([impact_version, projection_version])
    db.flush()

    team_a = make_team(db, 1, "AAA", "Alpha Test Club")
    team_b = make_team(db, 2, "BBB", "Beta Test Club")

    band_half_width = 6.3106 / 2  # the single distinct band width observed in production

    rosters: dict[str, list[Player]] = {"AAA": [], "BBB": []}
    for team_offset, (team, abbr) in enumerate(((team_a, "AAA"), (team_b, "BBB"))):
        for i in range(15):
            index = team_offset * 15 + i
            player = make_player(
                db,
                1000 + index,
                f"Fixture {abbr} {i:02d}",
                team,
                salary=None if i == 13 else 2_000_000 + index * 1_400_000,
                birth_date=date(2004 - (index % 14), 3, 1),
            )
            player.height_inches = 72 + (index % 12)
            for season in ("2024-25", "2025-26"):
                _season_stat_rows(db, player, index, season, now, team_id=team.id)
            # The final AAA player is intentionally unmodelled.
            if not (abbr == "AAA" and i == 14):
                tei = -3.0 + (index % 15) * 0.5
                db.add(
                    PlayerImpactEstimate(
                        player_id=player.id,
                        season="2025-26",
                        model_version_id=impact_version.id,
                        tei=tei,
                        tei_low=tei - band_half_width,
                        tei_high=tei + band_half_width,
                        availability=0.55 + (index % 9) * 0.05,
                        minutes_estimate=8.0 + (index % 12) * 2.4,
                        inputs={"fixture": True},
                    )
                )
            rosters[abbr].append(player)

    for team in (team_a, team_b):
        for need_key, severity, pct in (
            ("three_point_volume", 0.62, 19.0),
            ("point_of_attack_defense", 0.41, 29.5),
            ("rim_protection", 0.18, 41.0),
            ("defensive_rebounding", 0.0, 67.0),
        ):
            db.add(
                TeamNeed(
                    team_id=team.id,
                    season="2025-26",
                    need_key=need_key,
                    severity=severity,
                    percentile=pct,
                    explanation=f"test fixture need ({need_key})",
                )
            )
    db.commit()
    return {
        "team_a": team_a,
        "team_b": team_b,
        "roster_a": rosters["AAA"],
        "roster_b": rosters["BBB"],
        "unmodeled": rosters["AAA"][14],
        "no_contract_a": rosters["AAA"][13],
        "no_contract_b": rosters["BBB"][13],
        "impact_version": impact_version,
        "projection_version": projection_version,
    }
