"""Idempotent ingestion jobs.

Every job upserts on natural keys, so re-running after a partial failure is safe and a
failed endpoint never destroys the last valid snapshot. All NBA.com access goes through
SwarNBAApiProvider — jobs never import nba_api directly."""

import asyncio
import math
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.calendar import ScopeState, state_of_scope, today_in_league
from app.canonical.rosters import resolve_roster_season
from app.canonical.seasons import Season, SeasonType, StatScope
from app.config import get_settings
from app.core.cache import get_cache
from app.core.logging import get_logger
from app.db.models import (
    Contract,
    ContractYear,
    Game,
    Player,
    PlayerSeasonStats,
    PlayerTeamHistory,
    RosterEntry,
    SeasonCalendar,
    Standing,
    Team,
    TeamSeasonStats,
)
from app.ingestion.contract_coverage import (
    contract_coverage,
    roster_side_unmatched,
    summarize,
)
from app.ingestion.identity import PlayerIdentityIndex
from app.ingestion.quality import _record as record_quality_issue
from app.ingestion.runs import sync_run
from app.integrations.contracts import get_contract_provider
from app.integrations.nba_api.canonical import fetch_result
from app.integrations.nba_api.provider import SwarNBAApiProvider, get_provider

logger = get_logger(__name__)


def _team_map(db: Session) -> dict[int, Team]:
    return {t.nba_team_id: t for t in db.scalars(select(Team)).all()}


def _player_map(db: Session) -> dict[int, Player]:
    return {p.nba_player_id: p for p in db.scalars(select(Player)).all()}


def _apply(entity: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        setattr(entity, key, value)


REGULAR = SeasonType.REGULAR.value

#: A roster refresh must cover at least this share of the teams the previous snapshot
#: covered, and at least this share of its player count, or it is rejected.
ROSTER_MIN_TEAM_COVERAGE = 0.9
ROSTER_MIN_PLAYER_RATIO = 0.5


class SnapshotRejectedError(RuntimeError):
    """A provider answered, but not with anything that could replace the stored snapshot.

    Distinct from a provider *failure*: the call succeeded and the payload is implausible
    (empty, or covering a fraction of the league). The previous snapshot is kept untouched
    and the run is recorded as failed.
    """


class PartialRefreshError(RuntimeError):
    """A multi-season refresh in which some seasons were written and some were not.

    The seasons that succeeded are committed and stay; the run is recorded as failed so the
    gap is visible, and the message names both lists.
    """


def _fetch_rows(endpoint: str, call, **params: Any) -> list[dict[str, Any]]:
    """One provider call, classified canonically (``ProviderResult``).

    A well-formed "no rows" comes back as ``[]``; any failure re-raises the original exception, so
    callers keep their existing error handling and the run records the real error class.
    """
    result, error = fetch_result(endpoint, call, **params)
    if error is not None:
        raise error
    return list(result.records)


def _empty_is_expected(season: str, season_type: SeasonType = SeasonType.REGULAR) -> bool:
    """Is "the provider has no rows" the right answer today for this scope?

    True only when the calendar says the games have not been played — 2026-27 regular-season
    statistics before 20 October 2026. For a season in progress or finished, no rows is a defect.
    """
    scope = StatScope(Season.parse(season), season_type)
    return state_of_scope(scope, today_in_league()) is ScopeState.UPCOMING


def _handle_empty(
    run, job: str, season: str, season_type: SeasonType = SeasonType.REGULAR
) -> None:
    """The provider answered with no rows: legitimate before the first game, a rejection after.

    Writing nothing is safe either way; what differs is the *report*. Before the first game the run
    succeeds with ``outcome: expected_empty`` — it is not a failing dataset. Afterwards it is a
    rejected snapshot, because *reporting success* while writing nothing hides a dataset going stale.
    """
    if _empty_is_expected(season, season_type):
        outcomes = dict((run.detail or {}).get("outcomes", {}))
        outcomes[season] = "expected_empty"
        run.detail = {
            **(run.detail or {}),
            "outcome": "expected_empty",
            "outcomes": outcomes,
            "season_type": season_type.value,
            "reason": f"{season} {season_type.value}-season games have not been played yet",
        }
        return
    raise SnapshotRejectedError(
        f"{job}: provider returned no rows for {season}; stored data left unchanged"
    )


def sync_teams(db: Session, provider: SwarNBAApiProvider | None = None) -> int:
    provider = provider or get_provider()
    with sync_run(db, "sync_teams") as run:
        records = asyncio.run(provider.fetch_teams())
        existing = _team_map(db)
        now = datetime.now(UTC)
        for record in records:
            values = {**record, "source_retrieved_at": now, "ingestion_run_id": run.id}
            team = existing.get(record["nba_team_id"])
            if team is None:
                db.add(Team(**values))
            else:
                _apply(team, values)
            run.rows_written += 1
        db.commit()
        return run.rows_written


def sync_players(db: Session, provider: SwarNBAApiProvider | None = None) -> int:
    provider = provider or get_provider()
    with sync_run(db, "sync_players") as run:
        records = asyncio.run(provider.fetch_players())
        existing = _player_map(db)
        now = datetime.now(UTC)
        for record in records:
            values = {**record, "source_retrieved_at": now, "ingestion_run_id": run.id}
            player = existing.get(record["nba_player_id"])
            if player is None:
                db.add(Player(**values))
            else:
                _apply(player, values)
            run.rows_written += 1
        db.commit()
        return run.rows_written


def _check_roster_snapshot_plausible(
    usable: list[dict[str, Any]],
    teams: dict[int, Team],
    current: list[RosterEntry],
) -> None:
    """Refuse to replace a roster snapshot with one that cannot be the league.

    The previous version marked every current row superseded *before* looking at what had
    come back, so a provider that answered with an empty list left the league with no
    rosters at all. This runs first and touches nothing.
    """
    if not usable:
        raise SnapshotRejectedError(
            "sync_rosters: provider returned no roster rows for any known team; "
            "previous snapshot kept"
        )
    if not current:
        # A first snapshot of a season (2026-27 rosters appearing) has nothing to compare with,
        # but it still has to look like a league: most of the teams Pivot knows.
        known = len(teams)
        covered = len({r["nba_team_id"] for r in usable})
        needed = math.ceil(round(ROSTER_MIN_TEAM_COVERAGE * known, 9))
        if known and covered < needed:
            raise SnapshotRejectedError(
                f"sync_rosters: first snapshot covers {covered} of {known} known teams "
                f"(need {needed}); nothing written"
            )
        return
    previous_teams = {e.team_id for e in current}
    new_teams = {teams[r["nba_team_id"]].id for r in usable}
    needed = math.ceil(round(ROSTER_MIN_TEAM_COVERAGE * len(previous_teams), 9))
    if len(new_teams & previous_teams) < needed:
        raise SnapshotRejectedError(
            f"sync_rosters: snapshot covers {len(new_teams & previous_teams)} of the "
            f"{len(previous_teams)} teams the previous snapshot did (need {needed}); kept"
        )
    if len(usable) < ROSTER_MIN_PLAYER_RATIO * len(current):
        raise SnapshotRejectedError(
            f"sync_rosters: snapshot has {len(usable)} players against {len(current)} before; "
            "kept"
        )


def _dedupe_roster_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One row per player.

    Two rows for the same player and team are one fact seen twice. Two rows for *different*
    teams mean the player moved between the first team's fetch and the second's (the 30 rosters
    are fetched one after another): the later retrieval is the newer state, so it wins. Without
    this a trade during the fetch put the player on two current rosters.
    """
    best: dict[int, dict[str, Any]] = {}
    same_team = moved = 0
    for row in rows:
        previous = best.get(row["nba_player_id"])
        if previous is None:
            best[row["nba_player_id"]] = row
            continue
        if previous["nba_team_id"] == row["nba_team_id"]:
            same_team += 1
            continue
        moved += 1
        if (row.get("source_retrieved_at") or datetime.min.replace(tzinfo=UTC)) >= (
            previous.get("source_retrieved_at") or datetime.min.replace(tzinfo=UTC)
        ):
            best[row["nba_player_id"]] = row
    return list(best.values()), {"duplicate_rows": same_team, "player_on_two_teams": moved}


def sync_rosters(
    db: Session,
    season: str | None = None,
    provider=None,
    *,
    job_name: str = "sync_rosters",
    update_existing_bio: bool = True,
) -> int:
    """Refresh current rosters by *diffing* against the stored snapshot.

    ``update_existing_bio=False`` is the roster-only mode (``make refresh DATASETS=rosters ARGS=--roster-only``):
    memberships and the identities of players Pivot has never seen are written, but the position, height, weight,
    birth date and experience of players it already knows are left exactly as stored. Those fields feed the
    skill vectors and archetypes, so refreshing them is a deliberate step of its own, not a side effect of
    looking at who is on a roster (R8.2 activation found 6 newly filled birth dates alone move the acquisition
    battery's headline numbers).

    Membership that has not changed is **confirmed** — its ``source_retrieved_at`` moves
    forward and nothing else does, so ``valid_from`` keeps meaning "first observed on this
    team". A player seen on a new team gets a new row; a player no longer listed has his row
    closed (``is_current`` false, ``valid_to`` set). The previous implementation superseded and
    re-inserted every row on every run: at the worker's six-hour cadence that is ~1.5 million
    rows a year of identical membership, and ``player_team_history`` grew by one duplicate per
    player per run.

    Bio fields (height/weight/position/experience/birth date) are enriched from roster rows.
    A snapshot that cannot be the league is rejected before any row is touched
    (``SnapshotRejectedError``).
    """
    settings = get_settings()
    # R8.2: rosters follow the league year, not the statistics season. This is the *preferred*
    # roster season on purpose, not `resolve_roster_season(db)`: ingestion is what creates the
    # snapshot, so it must not fall back to an older one.
    season = season or settings.roster_season_preferred
    provider = provider or get_provider()
    with sync_run(db, job_name) as run:
        # Committed before anything can fail, so a rejected or failed run still says which
        # season's roster it was refreshing (the freshness report attributes runs by this).
        run.detail = {"season": season}
        db.commit()
        rows = _fetch_rows("rosters", lambda: provider.fetch_rosters(season), season=season)
        teams = _team_map(db)
        players = _player_map(db)
        now = datetime.now(UTC)

        usable = [r for r in rows if teams.get(r["nba_team_id"]) is not None]
        usable, duplicates = _dedupe_roster_rows(usable)
        current = list(
            db.scalars(
                select(RosterEntry).where(RosterEntry.season == season, RosterEntry.is_current)
            ).all()
        )
        _check_roster_snapshot_plausible(usable, teams, current)

        current_by_key = {(e.team_id, e.player_id): e for e in current}
        history_by_key = {
            (h.team_id, h.player_id): h
            for h in db.scalars(
                select(PlayerTeamHistory).where(PlayerTeamHistory.season == season)
            ).all()
        }
        seen: set[tuple[str, str]] = set()
        added = confirmed = 0
        for row in usable:
            team = teams[row["nba_team_id"]]
            player = players.get(row["nba_player_id"])
            is_new_player = player is None
            if player is None:
                # Player appears on an NBA.com roster but not in static data (e.g. new
                # signing): create the identity record from the roster row itself.
                player = Player(
                    nba_player_id=row["nba_player_id"],
                    full_name=row.get("player_name") or f"NBA Player {row['nba_player_id']}",
                    is_active=True,
                    source_record_id=str(row["nba_player_id"]),
                    source_retrieved_at=now,
                    ingestion_run_id=run.id,
                )
                db.add(player)
                db.flush()
                players[row["nba_player_id"]] = player
            for field in (
                "position",
                "birth_date",
                "height_inches",
                "weight_lbs",
                "years_experience",
            ):
                if row.get(field) is not None and (update_existing_bio or is_new_player):
                    setattr(player, field, row[field])
            key = (team.id, player.id)
            seen.add(key)
            retrieved = row.get("source_retrieved_at") or now
            entry = current_by_key.get(key)
            if entry is not None:
                entry.jersey_number = row.get("jersey_number")
                entry.position = row.get("position")
                entry.age = row.get("age")
                entry.source_record_id = row.get("source_record_id")
                entry.source_retrieved_at = retrieved
                entry.ingestion_run_id = run.id
                confirmed += 1
            else:
                db.add(
                    RosterEntry(
                        team_id=team.id,
                        player_id=player.id,
                        season=season,
                        jersey_number=row.get("jersey_number"),
                        position=row.get("position"),
                        age=row.get("age"),
                        is_current=True,
                        source_record_id=row.get("source_record_id"),
                        source_retrieved_at=retrieved,
                        valid_from=now,
                        ingestion_run_id=run.id,
                    )
                )
                added += 1
            history = history_by_key.get(key)
            if history is not None:
                # Already observed on this team this season: confirm, keep `observed_at`
                # as the first sighting.
                history.source_retrieved_at = retrieved
                history.ingestion_run_id = run.id
            else:
                history_by_key[key] = PlayerTeamHistory(
                    player_id=player.id,
                    team_id=team.id,
                    season=season,
                    observed_at=now,
                    source_record_id=row.get("source_record_id"),
                    source_retrieved_at=retrieved,
                    ingestion_run_id=run.id,
                )
                db.add(history_by_key[key])
            run.rows_written += 1

        closed = 0
        for key, entry in current_by_key.items():
            if key not in seen:
                entry.is_current = False
                entry.valid_to = now
                closed += 1
        run.detail = {
            "added": added,
            "confirmed": confirmed,
            "closed": closed,
            "season": season,
            "existing_player_bio_updated": update_existing_bio,
            **duplicates,
        }
        db.commit()
        return run.rows_written


def sync_standings(db: Session, season: str | None = None, provider=None) -> int:
    settings = get_settings()
    season = season or settings.current_season
    provider = provider or get_provider()
    with sync_run(db, "sync_standings") as run:
        rows = _fetch_rows("standings", lambda: provider.fetch_standings(season), season=season)
        if not rows:
            _handle_empty(run, "sync_standings", season)
            db.commit()
            return 0
        teams = _team_map(db)
        for row in rows:
            team = teams.get(row["nba_team_id"])
            if team is None:
                continue
            team.conference = row.get("conference")
            team.division = row.get("division")
            values = {
                "team_id": team.id,
                "season": season,
                "wins": row["wins"],
                "losses": row["losses"],
                "win_pct": row["win_pct"],
                "conference": row.get("conference"),
                "conference_rank": row.get("conference_rank"),
                "playoff_rank": row.get("playoff_rank"),
                "details": row.get("details", {}),
                "source_record_id": row.get("source_record_id"),
                "source_retrieved_at": row.get("source_retrieved_at"),
                "ingestion_run_id": run.id,
            }
            standing = db.scalar(
                select(Standing).where(Standing.team_id == team.id, Standing.season == season)
            )
            if standing is None:
                db.add(Standing(**values))
            else:
                _apply(standing, values)
            run.rows_written += 1
        db.commit()
        return run.rows_written


def _upsert_stat(db: Session, model, key_filter: tuple, values: dict[str, Any], counts: Counter) -> None:
    """Insert or update one season-stat row, counting whether the figures actually changed.

    ``changed`` is how a historical *correction* shows: the same season, refetched, with
    different numbers. The row is updated (the source is the authority) and the run says so.
    """
    stat = db.scalar(select(model).where(*key_filter))
    if stat is None:
        db.add(model(**values))
        counts["added"] += 1
        return
    if (stat.stats or {}) != (values.get("stats") or {}):
        counts["changed"] += 1
    else:
        counts["unchanged"] += 1
    _apply(stat, values)


def _run_seasons(run, job: str, seasons: list[str], season_type: SeasonType, one) -> None:
    """Run ``one(season)`` for each season, keeping what succeeds and reporting what does not."""
    done: list[str] = []
    failed: dict[str, str] = {}
    first_error: BaseException | None = None
    for season in seasons:
        try:
            if one(season):
                done.append(season)
        except Exception as exc:  # noqa: BLE001 — recorded per season
            failed[season] = f"{type(exc).__name__}: {exc}"[:300]
            first_error = first_error or exc
    if not failed:
        return
    if not done and first_error is not None:
        # Nothing was written: the first error says everything (and keeps its class).
        raise first_error
    raise PartialRefreshError(
        f"{job}: {len(done)} of {len(seasons)} seasons written ({', '.join(done) or 'none'}); "
        f"failed: {failed}"
    )


def sync_player_stats(
    db: Session,
    seasons: list[str] | None = None,
    provider=None,
    *,
    season_type: SeasonType = SeasonType.REGULAR,
) -> int:
    """Refresh player season statistics for ``seasons`` (default: the modelling window plus the
    league season once its regular season has started) and one season type.

    Preseason and regular-season rows are different populations and are stored under different
    ``season_type`` keys; neither can overwrite the other. A season for which the provider has no
    rows yet is not an error when the calendar says its games have not been played.
    """
    settings = get_settings()
    if seasons is None:
        seasons = (
            settings.sync_season_list
            if season_type is SeasonType.REGULAR
            else [settings.season_context.league_season.label]
        )
    provider = provider or get_provider()
    kwargs: dict[str, Any] = {} if season_type is SeasonType.REGULAR else {"season_type": season_type}
    with sync_run(db, "sync_player_stats") as run:
        players = _player_map(db)
        teams = _team_map(db)
        counts: Counter[str] = Counter()
        unknown_players: dict[int, str] = {}

        def one(season: str) -> bool:
            rows = _fetch_rows(
                "player_stats",
                lambda: provider.fetch_player_stats(season, **kwargs),
                season=season,
                season_type=season_type.value,
            )
            if not rows:
                _handle_empty(run, "sync_player_stats", season, season_type)
                return False
            if season_type is SeasonType.REGULAR:
                try:
                    rows = rows + _fetch_rows(
                        "player_estimated_metrics",
                        lambda: provider.fetch_player_estimated_metrics(season),
                        season=season,
                    )
                except Exception as exc:
                    # Estimated metrics are optional enrichment with a documented fallback;
                    # base+advanced remain authoritative.
                    logger.warning("estimated metrics unavailable for %s: %s", season, exc)
            for row in rows:
                player = players.get(row["nba_player_id"])
                if player is None:
                    # Counted, not hidden: a player the identity table does not know has no home for
                    # his row, and the run should say how many there were.
                    counts["skipped_unknown_player"] += 1
                    name = (row.get("stats") or {}).get("PLAYER_NAME")
                    unknown_players.setdefault(row["nba_player_id"], str(name or ""))
                    continue
                team = teams.get(row.get("nba_team_id") or -1)
                values = {
                    "player_id": player.id,
                    "team_id": team.id if team else None,
                    "season": season,
                    "season_type": season_type.value,
                    "stat_type": row["stat_type"],
                    "games_played": row.get("games_played"),
                    "minutes": row.get("minutes"),
                    "stats": row.get("stats", {}),
                    "source_record_id": row.get("source_record_id"),
                    "source_retrieved_at": row.get("source_retrieved_at"),
                    "ingestion_run_id": run.id,
                }
                _upsert_stat(
                    db,
                    PlayerSeasonStats,
                    (
                        PlayerSeasonStats.player_id == player.id,
                        PlayerSeasonStats.season == season,
                        PlayerSeasonStats.season_type == season_type.value,
                        PlayerSeasonStats.stat_type == row["stat_type"],
                    ),
                    values,
                    counts,
                )
                run.rows_written += 1
            db.commit()
            return True

        try:
            _run_seasons(run, "sync_player_stats", seasons, season_type, one)
        finally:
            if counts:
                run.detail = {**(run.detail or {}), "rows": dict(counts)}
            if unknown_players:
                run.detail = {
                    **(run.detail or {}),
                    "unknown_players": [
                        {"nba_player_id": k, "name": v} for k, v in sorted(unknown_players.items())
                    ][:25],
                }
        return run.rows_written


def sync_team_stats(
    db: Session,
    seasons: list[str] | None = None,
    provider=None,
    *,
    season_type: SeasonType = SeasonType.REGULAR,
) -> int:
    settings = get_settings()
    if seasons is None:
        seasons = (
            settings.sync_season_list
            if season_type is SeasonType.REGULAR
            else [settings.season_context.league_season.label]
        )
    provider = provider or get_provider()
    kwargs: dict[str, Any] = {} if season_type is SeasonType.REGULAR else {"season_type": season_type}
    with sync_run(db, "sync_team_stats") as run:
        teams = _team_map(db)
        counts: Counter[str] = Counter()

        def one(season: str) -> bool:
            rows = _fetch_rows(
                "team_stats",
                lambda: provider.fetch_team_stats(season, **kwargs),
                season=season,
                season_type=season_type.value,
            )
            if not rows:
                _handle_empty(run, "sync_team_stats", season, season_type)
                return False
            for row in rows:
                team = teams.get(row["nba_team_id"])
                if team is None:
                    counts["skipped_unknown_team"] += 1
                    continue
                values = {
                    "team_id": team.id,
                    "season": season,
                    "season_type": season_type.value,
                    "stat_type": row["stat_type"],
                    "stats": row.get("stats", {}),
                    "source_record_id": row.get("source_record_id"),
                    "source_retrieved_at": row.get("source_retrieved_at"),
                    "ingestion_run_id": run.id,
                }
                _upsert_stat(
                    db,
                    TeamSeasonStats,
                    (
                        TeamSeasonStats.team_id == team.id,
                        TeamSeasonStats.season == season,
                        TeamSeasonStats.season_type == season_type.value,
                        TeamSeasonStats.stat_type == row["stat_type"],
                    ),
                    values,
                    counts,
                )
                run.rows_written += 1
            db.commit()
            return True

        try:
            _run_seasons(run, "sync_team_stats", seasons, season_type, one)
        finally:
            if counts:
                run.detail = {**(run.detail or {}), "rows": dict(counts)}
        return run.rows_written


def sync_games(db: Session, season: str | None = None, provider=None) -> int:
    settings = get_settings()
    season = season or settings.current_season
    provider = provider or get_provider()
    with sync_run(db, "sync_games") as run:
        rows = _fetch_rows("games", lambda: provider.fetch_games(season), season=season)
        if not rows:
            _handle_empty(run, "sync_games", season)
            db.commit()
            return 0
        teams = _team_map(db)
        for row in rows:
            home = teams.get(row.get("home_nba_team_id") or -1)
            away = teams.get(row.get("away_nba_team_id") or -1)
            values = {
                "nba_game_id": row["nba_game_id"],
                "season": season,
                "game_date": row["game_date"],
                "home_team_id": home.id if home else None,
                "away_team_id": away.id if away else None,
                "home_score": row.get("home_score"),
                "away_score": row.get("away_score"),
                "status": row.get("status", "final"),
                "source_record_id": row.get("source_record_id"),
                "source_retrieved_at": row.get("source_retrieved_at"),
                "ingestion_run_id": run.id,
            }
            game = db.scalar(select(Game).where(Game.nba_game_id == row["nba_game_id"]))
            if game is None:
                db.add(Game(**values))
            else:
                _apply(game, values)
            run.rows_written += 1
        db.commit()
        return run.rows_written


def sync_season_calendar(db: Session, seasons: list[str] | None = None, provider=None) -> int:
    """Record when each season was actually played, from the same game log `sync_games` reads.

    Only the boundary is stored, not the games. The question this answers is "had this
    season been played when the trade was made?", which needs two dates per season; the
    1,230 rows behind them would be 12,000 rows across the corpus, and nothing else here
    asks a question of a historical game.

    The two dates are the first and last REGULAR-SEASON game. `LeagueGameLog` is queried
    for the regular season only, so the play-in and the playoffs are outside the window by
    construction — and they should be, because no trade can be made during them.
    """
    settings = get_settings()
    seasons = seasons or settings.corpus_season_list
    provider = provider or get_provider()
    with sync_run(db, "sync_season_calendar") as run:
        for season in seasons:
            rows = _fetch_rows(
                "games", lambda season=season: provider.fetch_games(season), season=season
            )
            dates = sorted(r["game_date"] for r in rows if r.get("game_date"))
            if not dates and _empty_is_expected(season):
                continue  # an unplayed season has no boundary to record, and that is not an issue
            if not dates:
                # A season with no game log is not a season with no games; it is a
                # provider answer this job cannot interpret, so nothing is written and
                # `feature_season_for` keeps saying it does not know.
                record_quality_issue(
                    db,
                    "season_calendar_empty",
                    f"{season}: the game log returned no dated games; "
                    "season boundary not recorded",
                )
                continue
            values = {
                "first_game_date": dates[0],
                "last_game_date": dates[-1],
                "game_count": len(dates),
                # Was defaulted to "nba_api", which wrote an unearned claim into the
                # row itself rather than merely rendering one.
                "source_provider": rows[0].get("source_provider") or "unknown",
                "source_record_id": f"leaguegamelog:{season}",
                "source_retrieved_at": rows[0].get("source_retrieved_at"),
                "ingestion_run_id": run.id,
            }
            entry = db.scalar(select(SeasonCalendar).where(SeasonCalendar.season == season))
            if entry is None:
                db.add(SeasonCalendar(season=season, **values))
            else:
                _apply(entry, values)
            run.rows_written += 1
        db.commit()
        return run.rows_written


def sync_contracts(db: Session) -> int:
    """Ingest contracts from the configured secondary provider. With no provider this
    records an explicit no-op run — salary features stay honestly unavailable."""
    contract_provider = get_contract_provider()
    with sync_run(db, "sync_contracts") as run:
        settings = get_settings()
        if contract_provider is None:
            # Report coverage even with no provider: the gap is the point, and an
            # operator should be able to see it before deciding to import anything.
            coverage = contract_coverage(
                db, resolve_roster_season(db), settings.cap_league_year
            )
            run.detail = {
                "provider": None,
                "note": "no contract provider configured",
                "coverage": coverage,
                "summary": summarize(coverage, []),
            }
            db.commit()
            return 0
        settings = get_settings()
        records = contract_provider.fetch_contracts()
        # Tiered identity resolution; see app/ingestion/identity.py. The old join was
        # `{full_name.lower(): p}` over 5,121 players with no disambiguation, so for the
        # 38 duplicated names a coin flip decided which player got the contract.
        index = PlayerIdentityIndex(db, resolve_roster_season(db))
        now = datetime.now(UTC)
        matched = 0
        unmatched: list[str] = []
        ambiguous: list[dict] = []
        methods: Counter[str] = Counter()
        for record in records:
            resolution = index.resolve(
                nba_player_id=record.nba_player_id, name=record.player_name
            )
            player = resolution.player
            if player is None:
                if resolution.method == "ambiguous":
                    # Never silently picked. The record is dropped and named, because a
                    # wrong binding is worse than a missing one: it puts one team's
                    # salary on another team's books.
                    ambiguous.append(
                        {
                            "name": record.player_name,
                            "candidates": [
                                {"nba_player_id": c.nba_player_id, "name": c.full_name}
                                for c in resolution.ambiguous_candidates
                            ],
                        }
                    )
                    record_quality_issue(
                        db,
                        "contract_ambiguous_player",
                        (
                            f"contract row for '{record.player_name}' matched "
                            f"{len(resolution.ambiguous_candidates)} players "
                            f"({', '.join(str(c.nba_player_id) for c in resolution.ambiguous_candidates)}); "
                            "not bound — a wrong binding puts one team's salary on "
                            "another team's books"
                        ),
                        severity="error",
                        entity=record.player_name,
                    )
                else:
                    unmatched.append(record.player_name)
                continue
            methods[resolution.method] += 1
            contract = db.scalar(select(Contract).where(Contract.player_id == player.id))
            if contract is None:
                contract = Contract(
                    player_id=player.id,
                    contract_type=record.contract_type,
                    signed_date=record.signed_date,
                    no_trade_clause=record.no_trade_clause,
                    source_provider=contract_provider.name,
                    source_name=record.source_name,
                    source_date=record.source_date,
                    source_retrieved_at=now,
                    ingestion_run_id=run.id,
                )
                db.add(contract)
                db.flush()
            else:
                contract.contract_type = record.contract_type
                contract.signed_date = record.signed_date
                contract.no_trade_clause = record.no_trade_clause
                contract.source_name = record.source_name
                contract.source_date = record.source_date
                contract.source_retrieved_at = now
            year = db.scalar(
                select(ContractYear).where(
                    ContractYear.contract_id == contract.id,
                    ContractYear.season == record.season,
                )
            )
            values = {
                "contract_id": contract.id,
                "season": record.season,
                "salary": record.salary,
                "guaranteed": record.guaranteed,
                "player_option": record.player_option,
                "team_option": record.team_option,
                "source_provider": contract_provider.name,
                "source_retrieved_at": now,
                "ingestion_run_id": run.id,
            }
            if year is None:
                db.add(ContractYear(**values))
            else:
                _apply(year, values)
            matched += 1
        db.flush()
        # Coverage is measured from the ROSTER side, after the write. A provider-side
        # match rate can read 98 % while the question the product needs answered — can
        # we compute this team's payroll — is 60 %.
        roster_season = resolve_roster_season(db)
        coverage = contract_coverage(db, roster_season, settings.cap_league_year)
        uncovered = roster_side_unmatched(db, roster_season, settings.cap_league_year)
        run.rows_written = matched
        run.detail = {
            "provider": contract_provider.name,
            "matched": matched,
            "match_methods": dict(methods),
            "unmatched_players": unmatched[:50],
            "unmatched_players_total": len(unmatched),
            "ambiguous_players": ambiguous[:50],
            "ambiguous_players_total": len(ambiguous),
            "rejected_rows": getattr(contract_provider, "rejected_rows", [])[:50],
            "coverage": coverage,
            "roster_players_without_salary": uncovered,
            "summary": summarize(coverage, uncovered),
        }
        logger.info("sync_contracts: %s", run.detail["summary"])
        db.commit()
        return matched


def prepare_league_season(db: Session, provider=None) -> int:
    """Stage the league's own season before the tools move to it.

    While the analysis season is still the previous one (the summer, preseason and the first two
    weeks of the new season) the tools read last season's rosters. This keeps the *incoming*
    season's rosters current under their own season key so the rollover finds them in place.
    It runs under its own job name (``prepare_league_rosters``): a roster that NBA.com has not
    published yet must not mark the dataset the tools are *reading* as failing.
    """
    settings = get_settings()
    context = settings.season_context
    # R8.2: the tools' own roster season is refreshed by `sync_rosters`. This only has work left
    # when the league's season is *not* the one the tools read rosters from (a pinned historical
    # session) — the incoming roster is then staged under its own job name.
    if not context.analysis_is_baseline or context.league_season.label == settings.roster_season_preferred:
        return 0
    return sync_rosters(
        db, context.league_season.label, provider, job_name="prepare_league_rosters"
    )


def sync_all(db: Session) -> dict[str, Any]:
    """Full refresh in dependency order. Individual failures are recorded and do not
    abort later jobs — the last valid snapshot always survives."""
    results: dict[str, Any] = {}
    history = get_settings().sync_season_list
    steps = [
        ("sync_teams", lambda: sync_teams(db)),
        ("sync_players", lambda: sync_players(db)),
        ("sync_rosters", lambda: sync_rosters(db)),
        ("prepare_league_season", lambda: prepare_league_season(db)),
        ("sync_standings", lambda: sum(sync_standings(db, season) for season in history)),
        ("sync_team_stats", lambda: sync_team_stats(db)),
        ("sync_player_stats", lambda: sync_player_stats(db)),
        ("sync_games", lambda: sync_games(db)),
        ("sync_season_calendar", lambda: sync_season_calendar(db)),
        ("sync_contracts", lambda: sync_contracts(db)),
    ]
    for name, step in steps:
        try:
            results[name] = {"status": "succeeded", "rows": step()}
        except Exception as exc:  # noqa: BLE001 — recorded, surfaced via /data-health
            results[name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    from app.ingestion.quality import validate_data

    results["validate_data"] = {"status": "succeeded", "issues": validate_data(db)}
    # Each job already bumped on success (see `ingestion/runs.sync_run`). This final bump
    # covers `validate_data`, which writes issue rows outside a sync run.
    get_cache().bump_data_version()
    return results
