"""The GOAT trial evaluation harness.

Purpose: when the owner starts BALLDONTLIE's 48-hour trial (5 requests/minute), spend the
trial answering the questions that decide whether to subscribe, with a *repeatable* script
instead of ad-hoc poking, and leave behind evidence that can be re-read without spending
another request.

What it is not: proof of anything until it has run against the live API. It has only ever
run against synthetic fixtures (``tests/unit/test_balldontlie_harness.py``), which verify the
harness logic, not the provider. Every threshold below is a *proposal* made before seeing
the data; ``docs/r8/balldontlie-goat-evaluation.md`` explains each and the owner may change them.

How it spends the trial:

* Requests are paced by the client (15 s apart on the trial tier) and **every response is
  cached on disk** under the output directory. Re-running, or running the analysis offline
  with ``--replay``, costs no requests.
* A request **budget** caps the run. The default plan needs roughly 130 requests (~35 minutes
  at trial pacing); ``plan_summary()`` prints the exact figure.
* Probes are independent. One failing endpoint (a tier restriction, a 5xx) is recorded as
  that probe's finding and the run continues.
* Output is written to a gitignored directory; nothing is committed and no credential is
  written anywhere.

Comparisons are made against Pivot's *own existing* data (nba_api season stats, standings,
games, the contract snapshot). Agreement with NBA.com is the only accuracy test available
without a second paid source, and it is only a test of agreement, not of truth.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.canonical.records import ContractAggregateRecord, ProviderResult, ProviderStatus
from app.canonical.rosters import current_roster_clause
from app.canonical.seasons import Season, SeasonType
from app.config import get_settings
from app.db.models import (
    Contract,
    ContractYear,
    Game,
    Player,
    PlayerSeasonStats,
    ProviderEntityId,
    RosterEntry,
    Standing,
    TeamSeasonStats,
)
from app.integrations.balldontlie.client import BalldontlieClient, BalldontlieError
from app.integrations.balldontlie.crosswalk import crosswalk_players, crosswalk_teams
from app.integrations.balldontlie.provider import BalldontlieProvider, tier_allows

#: Historical seasons probed for depth: one page each, newest to oldest. The list is a
#: sample, not a claim about coverage; the report states the oldest season that answered.
DEPTH_SEASONS = [2024, 2022, 2019, 2016, 2013, 2010, 2005, 2000]

#: Proposed acceptance thresholds, fixed *before* any live data was seen.
THRESHOLDS = {
    "active_player_link_rate": 0.95,
    "team_link_rate": 1.0,
    "game_match_rate": 0.99,
    "season_average_agreement": 0.95,
    "advanced_field_fill": 0.90,
    "contract_roster_coverage": 0.90,
    "contract_value_agreement": 0.90,
    #: Added in R8.1, before any live data: share of linked active players whose BALLDONTLIE team is
    #: the team Pivot's current roster has them on; and share of teams whose per-game points agree.
    "roster_agreement": 0.90,
    "team_average_agreement": 0.95,
}
#: Per-game points agree if within this many points of NBA.com's.
PTS_TOLERANCE = 0.15


class BudgetExhaustedError(RuntimeError):
    pass


@dataclass
class Finding:
    probe: str
    area: str
    #: pass | fail | inconclusive (ran, could not decide / incomplete) | restricted (skipped: outside
    #: the plan) | error (blocked or failed) | not_run (excluded with --only)
    status: str
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)
    requests: int = 0


@dataclass
class Context:
    provider: BalldontlieProvider
    db: Session
    season: Season
    out_dir: Path
    budget: int
    findings: list[Finding] = field(default_factory=list)
    #: Replaying recorded responses costs nothing, so the budget does not apply.
    replay: bool = False
    #: The season whose rosters stand for "now". It is the league's own season once Pivot has staged
    #: that season's rosters (the summer and preseason, when ``season`` is the last completed one),
    #: otherwise ``season``. Rosters, never statistics, are compared on this.
    roster_season: Season | None = None

    @property
    def live_season(self) -> Season:
        return self.roster_season or self.season

    @property
    def client(self) -> BalldontlieClient:
        return self.provider.client

    def left(self) -> int:
        return self.budget - self.client.request_count

    def need(self, n: int) -> None:
        if not self.replay and self.left() < n:
            raise BudgetExhaustedError(
                f"{self.left()} requests left in the budget, this probe needs up to {n}"
            )


# --------------------------------------------------------------------- response cache


def cache_key(path: str, params: dict[str, Any] | None) -> str:
    """Stable per-request key. ``None`` parameters are never sent, so they are not part of the key
    (R8.3: the first live run keyed them, which made a harmless refactor invalidate the cache)."""
    sent = {k: v for k, v in (params or {}).items() if v is not None}
    return hashlib.sha1(  # noqa: S324 - cache key, not security
        json.dumps([path, sent], sort_keys=True, default=str).encode()
    ).hexdigest()


def reindex_cache(directory: Path) -> int:
    """Rename files written under the R8.2-era key (which hashed ``None`` parameters) to the
    current key, keeping the originals. Returns how many files were added."""
    added = 0
    for file in list(directory.glob("*.json")):
        record = json.loads(file.read_text())
        target = directory / f"{cache_key(record['path'], record.get('params'))}.json"
        if not target.exists():
            target.write_text(file.read_text())
            added += 1
    return added


def install_disk_cache(client: BalldontlieClient, directory: Path, *, replay_only: bool) -> None:
    """Make ``client.get`` read/write a per-request JSON file. Re-runs cost nothing."""
    directory.mkdir(parents=True, exist_ok=True)
    real_get = client.get

    def cached_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        key = cache_key(path, params)
        file = directory / f"{key}.json"
        if file.is_file():
            return json.loads(file.read_text())["payload"]
        if replay_only:
            raise BalldontlieError(
                ProviderStatus.UNAVAILABLE, path, "not recorded; replay mode makes no requests"
            )
        payload = real_get(path, params)
        file.write_text(
            json.dumps(
                {
                    "path": path,
                    "params": params,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "payload": payload,
                },
                default=str,
            )
        )
        return payload

    client.get = cached_get  # type: ignore[method-assign]


# ------------------------------------------------------------------------- helpers


def fill_rates(rows: list[dict[str, Any]], keys: list[str] | None = None) -> dict[str, float]:
    """Share of rows where each key is present and non-null."""
    if not rows:
        return {}
    keys = keys or sorted({k for r in rows for k in r})
    return {k: round(sum(r.get(k) is not None for r in rows) / len(rows), 4) for k in keys}


def _bdl_to_nba_player_ids(db: Session) -> dict[str, int]:
    """BALLDONTLIE player id -> NBA.com player id, via the crosswalk table."""
    out: dict[str, int] = {}
    links = db.scalars(
        select(ProviderEntityId).where(
            ProviderEntityId.entity_type == "player", ProviderEntityId.provider == "balldontlie"
        )
    ).all()
    players = {p.id: p.nba_player_id for p in db.scalars(select(Player)).all()}
    for link in links:
        if link.internal_id in players:
            out[link.provider_id] = players[link.internal_id]
    return out


def _result_to_finding(
    probe: str, area: str, result: ProviderResult[Any], ctx: Context
) -> Finding | None:
    """A finding for a call that produced no usable answer, or None if it did."""
    if result.has_answer:
        return None
    status = "restricted" if result.status == ProviderStatus.TIER_RESTRICTED else "error"
    return Finding(
        probe,
        area,
        status,
        f"{result.meta.endpoint}: {result.status.value}: {result.error}",
        {"provider_status": result.status.value},
        result.meta.requests,
    )


def pick_roster_season(db: Session, comparison: Season) -> Season:
    """The season whose current rosters represent "now" for a comparison on ``comparison``.

    Unscoped ``RosterEntry.is_current`` was R8's trap: with two seasons staged it mixes them (a
    player on one team in 2025-26 and another in 2026-27 sits on both). Every roster read here goes
    through ``current_roster_clause(season)`` instead.
    """
    league = get_settings().season_context.league_season
    if league != comparison and (
        db.scalar(select(func.count(RosterEntry.id)).where(current_roster_clause(league)))
    ):
        return league
    return comparison


def select_sample_players(
    db: Session, season: str, roster_season: str | None = None
) -> dict[str, list[int]]:
    """Deterministic representative players from Pivot's own data, as NBA.com ids.

    starters / reserves by minutes per game, rookies by experience, "moved" = whose stat-row
    team differs from the current roster team (traded since the season ended).
    ``season`` scopes the statistics; ``roster_season`` (default: ``season``) scopes the roster.
    """
    roster_season = roster_season or season
    rows = db.execute(
        select(Player.nba_player_id, PlayerSeasonStats.minutes, PlayerSeasonStats.team_id)
        .join(PlayerSeasonStats, PlayerSeasonStats.player_id == Player.id)
        .where(
            PlayerSeasonStats.season == season,
            PlayerSeasonStats.season_type == "regular",
            PlayerSeasonStats.stat_type == "base",
            PlayerSeasonStats.games_played >= 40,
        )
        .order_by(PlayerSeasonStats.minutes.desc(), Player.nba_player_id)
    ).all()
    sample = {
        "starters": [r.nba_player_id for r in rows[:6]],
        "reserves": [r.nba_player_id for r in rows[-6:]],
    }
    rookies = db.scalars(
        select(Player.nba_player_id)
        .join(RosterEntry, RosterEntry.player_id == Player.id)
        .where(current_roster_clause(roster_season), Player.years_experience == 0)
        .order_by(Player.nba_player_id)
        .limit(6)
    ).all()
    sample["rookies"] = list(rookies)
    current_team = {
        e.player_id: e.team_id
        for e in db.scalars(select(RosterEntry).where(current_roster_clause(roster_season))).all()
    }
    moved: list[int] = []
    for player_id, nba_id, team_id in db.execute(
        select(Player.id, Player.nba_player_id, PlayerSeasonStats.team_id)
        .join(PlayerSeasonStats, PlayerSeasonStats.player_id == Player.id)
        .where(PlayerSeasonStats.season == season, PlayerSeasonStats.stat_type == "base")
        .order_by(Player.nba_player_id)
    ):
        if player_id in current_team and team_id and current_team[player_id] != team_id:
            moved.append(nba_id)
    sample["moved_teams"] = moved[:6]
    return sample


# ---------------------------------------------------------------------------- probes


def probe_identity(ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    teams = ctx.provider.teams()
    failure = _result_to_finding("P1", "identity", teams, ctx)
    if failure:
        return [failure]
    team_report = crosswalk_teams(ctx.db, list(teams.records))
    out.append(
        Finding(
            "P1",
            "identity",
            "pass" if team_report.link_rate >= THRESHOLDS["team_link_rate"] else "fail",
            f"{team_report.linked}/{team_report.total} teams linked to Pivot teams",
            team_report.summary() | {"unresolved": team_report.unresolved},
            teams.meta.requests,
        )
    )
    active = ctx.provider.active_players()
    failure = _result_to_finding("P2", "identity", active, ctx)
    if failure:
        out.append(failure)
        return out
    report = crosswalk_players(ctx.db, list(active.records), season=ctx.live_season.label)
    ctx.db.commit()
    out.append(
        Finding(
            "P2",
            "identity",
            "pass" if report.link_rate >= THRESHOLDS["active_player_link_rate"] else "fail",
            f"{report.linked}/{report.total} active players linked "
            f"({report.link_rate:.1%}); {len(report.ambiguous)} ambiguous, "
            f"{len(report.unresolved)} unresolved",
            report.summary()
            | {
                "ambiguous_examples": report.ambiguous[:10],
                "unresolved_examples": report.unresolved[:10],
            },
            active.meta.requests,
        )
    )
    out.append(_roster_agreement(ctx, list(active.records)))
    return out


def _roster_agreement(ctx: Context, records: list[Any]) -> Finding:
    """P2b: does BALLDONTLIE put each linked player on the team Pivot's roster does? No request.

    The active-player list already says which team each player is on, so "current rosters" costs
    nothing extra to test. Compared against ``ctx.live_season``'s current roster (the league
    season's once staged) — a disagreement is either a stale Pivot roster or a stale provider, and the
    finding does not say which.
    """
    team_internal = {
        r.provider_id: r.internal_id
        for r in ctx.db.scalars(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == "team", ProviderEntityId.provider == "balldontlie"
            )
        )
    }
    player_internal = {
        r.provider_id: r.internal_id
        for r in ctx.db.scalars(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == "player", ProviderEntityId.provider == "balldontlie"
            )
        )
    }
    pivot_team = {
        e.player_id: e.team_id
        for e in ctx.db.scalars(
            select(RosterEntry).where(current_roster_clause(ctx.live_season))
        ).all()
    }
    compared = agree = not_on_roster = 0
    examples: list[dict[str, Any]] = []
    for rec in records:
        internal = player_internal.get(rec.provider_id)
        if internal is None or not rec.team_provider_id:
            continue
        if internal not in pivot_team:
            not_on_roster += 1
            continue
        compared += 1
        if team_internal.get(rec.team_provider_id) == pivot_team[internal]:
            agree += 1
        elif len(examples) < 10:
            examples.append({"player": rec.full_name, "balldontlie_team": rec.team_provider_id})
    rate = agree / compared if compared else 0.0
    status = (
        "inconclusive"
        if not compared
        else ("pass" if rate >= THRESHOLDS["roster_agreement"] else "fail")
    )
    return Finding(
        "P2b",
        "identity",
        status,
        f"{agree}/{compared} linked players are on the same team in BALLDONTLIE and in Pivot's "
        f"{ctx.live_season.label} roster ({rate:.1%}); {not_on_roster} linked players are on no "
        "Pivot roster",
        {
            "compared": compared,
            "agree": agree,
            "linked_not_on_a_pivot_roster": not_on_roster,
            "roster_season": ctx.live_season.label,
            "disagreement_examples": examples,
        },
        0,
    )


def probe_games(ctx: Context) -> list[Finding]:
    ctx.need(16)
    out: list[Finding] = []
    result = ctx.provider.games(ctx.season, SeasonType.REGULAR)
    failure = _result_to_finding("P3", "games", result, ctx)
    if failure:
        return [failure]
    team_by_bdl = {
        r.provider_id: r.internal_id
        for r in ctx.db.scalars(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == "team", ProviderEntityId.provider == "balldontlie"
            )
        )
    }
    ours = {
        (g.game_date, g.home_team_id, g.away_team_id): g
        for g in ctx.db.scalars(select(Game).where(Game.season == ctx.season.label))
    }
    matched = score_equal = date_mismatch = 0
    for game in result.records:
        key = (
            game.game_date,
            team_by_bdl.get(game.home_team_provider_id),
            team_by_bdl.get(game.away_team_provider_id),
        )
        mine = ours.get(key)
        if mine is None:
            date_mismatch += 1
            continue
        matched += 1
        if (mine.home_score, mine.away_score) == (game.home_score, game.away_score):
            score_equal += 1
    rate = matched / len(result.records) if result.records else 0.0
    out.append(
        Finding(
            "P3",
            "games",
            "pass" if rate >= THRESHOLDS["game_match_rate"] else "fail",
            f"{matched}/{len(result.records)} regular-season games matched nba_api games on "
            f"(date, home, away); scores equal on {score_equal}",
            {
                "provider_games": len(result.records),
                "pivot_games": len(ours),
                "matched": matched,
                "unmatched": date_mismatch,
                "scores_equal": score_equal,
                "warnings": list(result.warnings)[:5],
            },
            result.meta.requests,
        )
    )
    playoffs = ctx.provider.games(ctx.season, SeasonType.PLAYOFFS, max_pages=5)
    overlap = {g.provider_id for g in result.records} & {g.provider_id for g in playoffs.records}
    out.append(
        Finding(
            "P3b",
            "games",
            "pass" if not overlap else "fail",
            f"season_type separation: {len(playoffs.records)} playoff games, "
            f"{len(overlap)} ids also present in the regular-season pull",
            {"playoff_games": len(playoffs.records), "overlap": len(overlap)},
            playoffs.meta.requests,
        )
    )
    return out


def probe_standings(ctx: Context) -> list[Finding]:
    result = ctx.provider.standings(ctx.season)
    failure = _result_to_finding("P4", "standings", result, ctx)
    if failure:
        return [failure]
    team_by_bdl = {
        r.provider_id: r.internal_id
        for r in ctx.db.scalars(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == "team", ProviderEntityId.provider == "balldontlie"
            )
        )
    }
    ours = {
        s.team_id: (s.wins, s.losses)
        for s in ctx.db.scalars(select(Standing).where(Standing.season == ctx.season.label))
    }
    agree = sum(
        ours.get(team_by_bdl.get(r.team_provider_id, "")) == (r.wins, r.losses)
        for r in result.records
    )
    return [
        Finding(
            "P4",
            "standings",
            "pass" if result.records and agree == len(result.records) else "fail",
            f"{agree}/{len(result.records)} team records equal nba_api standings",
            {"rows": len(result.records)},
            result.meta.requests,
        )
    ]


def _first(values: dict[str, Any], *names: str) -> float | None:
    lowered = {k.lower(): v for k, v in values.items()}
    for name in names:
        if lowered.get(name) is not None:
            return float(lowered[name])
    return None


def probe_season_averages(ctx: Context) -> list[Finding]:
    ctx.need(12)
    result = ctx.provider.player_season_stats(ctx.season, SeasonType.REGULAR, stat_type="base")
    failure = _result_to_finding("P5", "stats", result, ctx)
    if failure:
        return [failure]
    nba_ids = _bdl_to_nba_player_ids(ctx.db)
    mine = {
        p.nba_player_id: (s.games_played, s.stats or {})
        for s, p in ctx.db.execute(
            select(PlayerSeasonStats, Player)
            .join(Player, Player.id == PlayerSeasonStats.player_id)
            .where(
                PlayerSeasonStats.season == ctx.season.label,
                PlayerSeasonStats.season_type == "regular",
                PlayerSeasonStats.stat_type == "base",
            )
        )
    }
    compared = per_game = totals = agree = 0
    for row in result.records:
        nba_id = nba_ids.get(row.player_provider_id)
        if nba_id is None or nba_id not in mine:
            continue
        games, base = mine[nba_id]
        bdl_pts = _first(row.values, "pts")
        pivot_pts = base.get("PTS")
        if bdl_pts is None or pivot_pts is None or not games or games < 20:
            continue
        compared += 1
        if abs(bdl_pts - pivot_pts) <= PTS_TOLERANCE:
            per_game += 1
            agree += 1
        elif abs(bdl_pts - pivot_pts * games) <= max(2.0, 0.01 * pivot_pts * games):
            totals += 1
    aggregation = (
        "per_game" if per_game > totals else "totals" if totals > per_game else "undetermined"
    )
    rate = agree / compared if compared else 0.0
    status = "inconclusive" if not compared else (
        "pass" if rate >= THRESHOLDS["season_average_agreement"] else "fail"
    )
    return [
        Finding(
            "P5",
            "stats",
            status,
            f"{agree}/{compared} players' PTS equal nba_api per-game within {PTS_TOLERANCE}; "
            f"aggregation looks {aggregation}",
            {
                "provider_rows": len(result.records),
                "compared": compared,
                "agree_per_game": per_game,
                "agree_as_totals": totals,
                "inferred_aggregation": aggregation,
                "stat_keys": sorted({k for r in result.records[:50] for k in r.values}),
                "fill_rates": fill_rates([r.values for r in result.records]),
            },
            result.meta.requests,
        )
    ]


def probe_team_averages(ctx: Context) -> list[Finding]:
    """P5b: team per-game points vs Pivot's team statistics (one request for all 30 teams)."""
    ctx.need(3)
    result = ctx.provider.team_season_stats(
        ctx.season, SeasonType.REGULAR, category="general", stat_type="base"
    )
    failure = _result_to_finding("P5b", "stats", result, ctx)
    if failure:
        return [failure]
    team_internal = {
        r.provider_id: r.internal_id
        for r in ctx.db.scalars(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == "team", ProviderEntityId.provider == "balldontlie"
            )
        )
    }
    mine = {
        t.team_id: (t.stats or {})
        for t in ctx.db.scalars(
            select(TeamSeasonStats).where(
                TeamSeasonStats.season == ctx.season.label,
                TeamSeasonStats.season_type == "regular",
                TeamSeasonStats.stat_type == "base",
            )
        )
    }
    compared = agree = 0
    for row in result.records:
        internal = team_internal.get(row.team_provider_id)
        pivot = mine.get(internal or "")
        bdl_pts = _first(row.values, "pts")
        if bdl_pts is None or not pivot or pivot.get("PTS") is None:
            continue
        compared += 1
        games = pivot.get("GP") or 1
        # Per-game as documented, or season totals (the aggregation is undocumented).
        if abs(bdl_pts - pivot["PTS"]) <= PTS_TOLERANCE or abs(bdl_pts - pivot["PTS"] * games) <= max(
            2.0, 0.01 * pivot["PTS"] * games
        ):
            agree += 1
    rate = agree / compared if compared else 0.0
    status = (
        "inconclusive"
        if not compared
        else ("pass" if rate >= THRESHOLDS["team_average_agreement"] else "fail")
    )
    return [
        Finding(
            "P5b",
            "stats",
            status,
            f"{agree}/{compared} teams' PTS equal nba_api's team per-game (or season total) "
            f"within tolerance ({rate:.1%})",
            {
                "provider_rows": len(result.records),
                "compared": compared,
                "agree": agree,
                "stat_keys": sorted({k for r in result.records[:30] for k in r.values}),
            },
            result.meta.requests,
        )
    ]


_ADVANCED_ALIASES = {
    "OFF_RATING": ("off_rating", "offensive_rating"),
    "DEF_RATING": ("def_rating", "defensive_rating"),
    "NET_RATING": ("net_rating",),
    "TS_PCT": ("ts_pct", "true_shooting_percentage"),
    "USG_PCT": ("usg_pct", "usage_percentage"),
    "PIE": ("pie",),
    "PACE": ("pace",),
    "AST_PCT": ("ast_pct", "assist_percentage"),
}


def probe_advanced(ctx: Context) -> list[Finding]:
    ctx.need(10)
    result = ctx.provider.player_season_stats(
        ctx.season, SeasonType.REGULAR, category="general", stat_type="advanced"
    )
    failure = _result_to_finding("P6", "advanced", result, ctx)
    if failure:
        return [failure]
    nba_ids = _bdl_to_nba_player_ids(ctx.db)
    mine = {
        p.nba_player_id: (s.stats or {})
        for s, p in ctx.db.execute(
            select(PlayerSeasonStats, Player)
            .join(Player, Player.id == PlayerSeasonStats.player_id)
            .where(
                PlayerSeasonStats.season == ctx.season.label,
                PlayerSeasonStats.season_type == "regular",
                PlayerSeasonStats.stat_type == "advanced",
            )
        )
    }
    keys = {k.lower() for r in result.records for k in r.values}
    mapping = {
        nba: next((a for a in aliases if a in keys), None)
        for nba, aliases in _ADVANCED_ALIASES.items()
    }
    comparisons: dict[str, dict[str, Any]] = {}
    for nba_key, bdl_key in mapping.items():
        if bdl_key is None:
            comparisons[nba_key] = {"status": "no matching provider field"}
            continue
        diffs: list[float] = []
        for row in result.records:
            nba_id = nba_ids.get(row.player_provider_id)
            theirs = _first(row.values, bdl_key)
            ours = (mine.get(nba_id) or {}).get(nba_key) if nba_id else None
            if theirs is not None and ours is not None:
                diffs.append(abs(theirs - float(ours)))
        comparisons[nba_key] = {
            "bdl_field": bdl_key,
            "compared": len(diffs),
            "median_abs_diff": round(statistics.median(diffs), 4) if diffs else None,
            "max_abs_diff": round(max(diffs), 4) if diffs else None,
        }
    mapped = [v for v in mapping.values() if v]
    fill = fill_rates([r.values for r in result.records], mapped)
    mean_fill = statistics.mean(fill.values()) if fill else 0.0
    return [
        Finding(
            "P6",
            "advanced",
            "pass" if mapped and mean_fill >= THRESHOLDS["advanced_field_fill"] else "fail",
            f"{len(mapped)}/{len(mapping)} NBA.com advanced fields have a provider counterpart; "
            f"mean fill {mean_fill:.1%}",
            {
                "provider_fields": sorted(keys),
                "mapping": mapping,
                "comparisons": comparisons,
                "fill_rates": fill,
            },
            result.meta.requests,
        )
    ]


def probe_other_categories(ctx: Context) -> list[Finding]:
    """One page each: what the clutch/defense/shooting/hustle/tracking families return."""
    ctx.need(10)
    out: list[Finding] = []
    for category, stat_type in (
        ("clutch", "base"),
        ("defense", "overall"),
        ("shooting", "by_zone"),
        ("tracking", "possessions"),
        ("playtype", "isolation"),
        ("hustle", None),
        ("shotdashboard", "overall"),
    ):
        result = ctx.provider.player_season_stats(
            ctx.season,
            SeasonType.REGULAR,
            category=category,
            stat_type=stat_type,
            max_pages=1,
            sample=True,
        )
        failure = _result_to_finding(f"P7-{category}", "advanced", result, ctx)
        if failure:
            out.append(failure)
            continue
        out.append(
            Finding(
                f"P7-{category}",
                "advanced",
                "inconclusive",
                f"{category}/{stat_type or '-'}: {len(result.records)} rows on page 1",
                {
                    "fields": sorted({k for r in result.records for k in r.values}),
                    "fill_rates": fill_rates([r.values for r in result.records]),
                },
                result.meta.requests,
            )
        )
    for category, stat_type in (
        ("general", "base"),
        ("general", "advanced"),
        ("tracking", "passing"),
        ("playtype", "isolation"),
        ("hustle", None),
    ):
        teams = ctx.provider.team_season_stats(
            ctx.season, SeasonType.REGULAR, category=category, stat_type=stat_type, max_pages=1,
            sample=True,
        )
        failure = _result_to_finding(f"P7-team-{category}-{stat_type}", "team_stats", teams, ctx)
        out.append(
            failure
            or Finding(
                f"P7-team-{category}-{stat_type}",
                "team_stats",
                "pass" if len(teams.records) == 30 else "fail",
                f"{len(teams.records)} team rows",
                {"fields": sorted({k for r in teams.records for k in r.values})},
                teams.meta.requests,
            )
        )
    return out


def probe_depth(ctx: Context) -> list[Finding]:
    ctx.need(len(DEPTH_SEASONS))
    seen: dict[int, int | str] = {}
    oldest: int | None = None
    for year in DEPTH_SEASONS:
        result = ctx.provider.player_season_stats(
            year, SeasonType.REGULAR, stat_type="base", max_pages=1, sample=True
        )
        if not result.has_answer:
            seen[year] = result.status.value
            continue
        seen[year] = len(result.records)
        if result.records:
            oldest = year
    return [
        Finding(
            "P8",
            "history",
            "inconclusive",
            f"oldest sampled season with player season averages: {oldest}",
            {"first_page_rows_by_season": seen, "sampled": DEPTH_SEASONS},
            len(DEPTH_SEASONS),
        )
    ]


def probe_contracts(ctx: Context) -> list[Finding]:
    ctx.need(31)
    team_ids = {
        r.internal_id: r.provider_id
        for r in ctx.db.scalars(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == "team", ProviderEntityId.provider == "balldontlie"
            )
        )
    }
    cap_season = Season.parse(get_settings().cap_league_year)
    rostered: dict[str, set[int]] = {}
    # Contracts are for the cap year, so they are compared with that year's roster when Pivot has
    # staged it, else with the roster the tools are reading (and the finding says which).
    staged = ctx.db.scalar(
        select(func.count(RosterEntry.id)).where(current_roster_clause(cap_season))
    )
    contract_roster = cap_season if staged else ctx.live_season
    for e in ctx.db.scalars(
        select(RosterEntry).where(current_roster_clause(contract_roster))
    ).all():
        rostered.setdefault(e.team_id, set()).add(e.player.nba_player_id)
    nba_ids = _bdl_to_nba_player_ids(ctx.db)
    mine: dict[int, int] = {}
    for year, player in ctx.db.execute(
        select(ContractYear, Player)
        .join(Contract, Contract.id == ContractYear.contract_id)
        .join(Player, Player.id == Contract.player_id)
        .where(ContractYear.season == cap_season.label)
    ):
        mine[player.nba_player_id] = year.salary
    covered = total = 0
    value_pairs: list[tuple[int, int]] = []
    null_cap_hit = rows = 0
    first_failure: Finding | None = None
    for internal_team, provider_team in sorted(team_ids.items()):
        result = ctx.provider.team_contracts(int(provider_team), cap_season)
        if not result.has_answer:
            first_failure = first_failure or _result_to_finding("P9", "contracts", result, ctx)
            break
        by_player = {c.player_provider_id: c for c in result.records}
        rows += len(by_player)
        null_cap_hit += sum(c.cap_hit is None for c in by_player.values())
        roster_nba = rostered.get(internal_team, set())
        total += len(roster_nba)
        have = {nba_ids.get(pid) for pid, c in by_player.items() if c.cap_hit is not None}
        covered += len(roster_nba & have)
        for pid, c in by_player.items():
            nba = nba_ids.get(pid)
            if nba in mine and c.cap_hit:
                value_pairs.append((mine[nba], c.cap_hit))
    if first_failure and not rows:
        return [first_failure]
    coverage = covered / total if total else 0.0
    within = (
        sum(abs(a - b) <= 0.02 * max(a, b) for a, b in value_pairs) / len(value_pairs)
        if value_pairs
        else None
    )
    findings = [
        Finding(
            "P9",
            "contracts",
            "pass" if coverage >= THRESHOLDS["contract_roster_coverage"] else "fail",
            f"{covered}/{total} players on the {contract_roster.label} rosters have a "
            f"{cap_season.label} cap hit ({coverage:.1%}); {null_cap_hit}/{rows} returned rows "
            "have a null cap hit",
            {
                "rostered": total,
                "covered": covered,
                "rows": rows,
                "null_cap_hit": null_cap_hit,
                "roster_season": contract_roster.label,
            },
            0,
        )
    ]
    findings.append(
        Finding(
            "P9b",
            "contracts",
            "inconclusive"
            if within is None
            else ("pass" if within >= THRESHOLDS["contract_value_agreement"] else "fail"),
            "no overlap with the contract snapshot to compare"
            if within is None
            else f"{within:.1%} of {len(value_pairs)} overlapping salaries within 2 % of the snapshot",
            {"pairs": len(value_pairs)},
            0,
        )
    )
    return findings


def probe_aggregates(ctx: Context) -> list[Finding]:
    ctx.need(12)
    nba_to_bdl = {v: k for k, v in _bdl_to_nba_player_ids(ctx.db).items()}
    sample = select_sample_players(ctx.db, ctx.season.label, ctx.live_season.label)
    chosen = [nba_to_bdl[n] for n in (sample["starters"][:4] + sample["reserves"][:4]) if n in nba_to_bdl]
    rows: list[ContractAggregateRecord] = []
    for provider_id in chosen[:8]:
        result = ctx.provider.contract_aggregate(int(provider_id))
        if not result.has_answer:
            failure = _result_to_finding("P10", "contracts", result, ctx)
            if failure:
                return [failure]
        rows.extend(result.records)
    wanted = ["contract_type", "guaranteed_at_signing", "total_guaranteed", "signed_using",
              "free_agent_year", "free_agent_status"]
    fill = {
        k: round(sum(getattr(r, k) is not None for r in rows) / len(rows), 4) if rows else None
        for k in wanted
    }
    return [
        Finding(
            "P10",
            "contracts",
            "inconclusive",
            f"{len(rows)} contract aggregates sampled; guarantee/option/no-trade fields are "
            "reported as the provider supplies them and never inferred",
            {
                "fill": fill,
                "notes_keys": sorted({k for r in rows for k in r.notes}),
                "provides_no_trade_clause": any("no" in str(k).lower() and "trade" in str(k).lower() for r in rows for k in r.notes),
            },
            len(rows),
        )
    ]


def probe_injuries(ctx: Context) -> list[Finding]:
    result = ctx.provider.injuries(max_pages=3)
    failure = _result_to_finding("P11", "injuries", result, ctx)
    if failure:
        return [failure]
    return [
        Finding(
            "P11",
            "injuries",
            "inconclusive",
            f"{len(result.records)} injury rows",
            {
                "statuses": sorted({r.status for r in result.records}),
                "with_return_date": sum(r.return_date is not None for r in result.records),
            },
            result.meta.requests,
        )
    ]


def probe_raw_endpoints(ctx: Context) -> list[Finding]:
    """Lineups, play-by-play and advanced box scores for a handful of games: shape and fill."""
    ctx.need(10)
    out: list[Finding] = []
    games = ctx.provider.games(ctx.season, SeasonType.REGULAR, max_pages=1, sample=True)
    if not games.has_answer or not games.records:
        return [Finding("P12", "raw", "error", "no games available to sample", {}, 0)]
    ids = [int(g.provider_id) for g in games.records[:3]]
    for name, path, params in (
        ("P12-lineups", "/lineups", {"game_ids[]": ids}),
        ("P12-plays", "/plays", {"game_id": ids[0]}),
        ("P12-advanced", "/v2/stats/advanced", {"game_ids[]": ids[:1], "per_page": 100}),
    ):
        try:
            payload = ctx.client.get(path, params)
        except BalldontlieError as exc:
            out.append(Finding(name, "raw", "restricted" if exc.status == ProviderStatus.TIER_RESTRICTED else "error", str(exc), {}, 0))
            continue
        data = payload.get("data") or []
        rows = [r for r in data if isinstance(r, dict)]
        out.append(
            Finding(
                name,
                "raw",
                "inconclusive",
                f"{len(rows)} rows; fields observed {len({k for r in rows for k in r})}",
                {
                    "fields": sorted({k for r in rows for k in r}),
                    "fill_rates": fill_rates(rows),
                    "lineups_are_starting_lineups_only": name == "P12-lineups",
                },
                1,
            )
        )
    return out


def probe_errors(ctx: Context) -> list[Finding]:
    """Error behaviour with a deliberately wrong key and a deliberately bad parameter."""
    bad = BalldontlieClient(
        "invalid-key-for-error-test",
        tier="trial",
        base_url=str(ctx.client._http.base_url),  # noqa: SLF001
        prefix=ctx.client.prefix,
    )
    try:
        result = BalldontlieProvider(bad).teams()
    finally:
        bad.close()
    out = [
        Finding(
            "P13",
            "errors",
            "pass" if result.status == ProviderStatus.UNAUTHORIZED else "fail",
            f"an invalid key yields {result.status.value}",
            {"status": result.status.value},
            1,
        )
    ]
    try:
        ctx.client.get("/games", {"seasons[]": ["not-a-year"]})
        out.append(Finding("P13b", "errors", "fail", "a malformed season was accepted", {}, 1))
    except BalldontlieError as exc:
        out.append(
            Finding(
                "P13b",
                "errors",
                "pass" if exc.status == ProviderStatus.SCHEMA_ERROR else "inconclusive",
                f"a malformed parameter yields {exc.status.value} (HTTP {exc.http_status})",
                {"status": exc.status.value},
                1,
            )
        )
    return out


@dataclass(frozen=True)
class PlanStep:
    id: str
    area: str
    endpoint: str
    estimated_requests: int
    run: Callable[[Context], list[Finding]]
    description: str


PLAN: list[PlanStep] = [
    PlanStep("P1-P2", "identity", "teams + players/active", 8, probe_identity,
             "Link teams and active players; report link rate, ambiguity, unresolved"),
    PlanStep("P3", "games", "games", 16, probe_games,
             "Regular-season games vs nba_api; season_type separation from playoffs"),
    PlanStep("P4", "standings", "standings", 1, probe_standings, "Standings vs nba_api"),
    PlanStep("P5", "stats", "season_averages/general?type=base", 8, probe_season_averages,
             "Per-game vs totals; agreement with nba_api per-game rows"),
    PlanStep("P5b", "stats", "team_season_averages/general?type=base", 1, probe_team_averages,
             "Team per-game points vs nba_api team statistics"),
    PlanStep("P6", "advanced", "season_averages/general?type=advanced", 8, probe_advanced,
             "Advanced-field coverage and agreement"),
    PlanStep("P7", "advanced", "other categories", 6, probe_other_categories,
             "What clutch/defense/shooting/team categories actually contain"),
    PlanStep("P8", "history", "season_averages (older seasons)", len(DEPTH_SEASONS), probe_depth,
             "Historical depth by sampling older seasons"),
    PlanStep("P9", "contracts", "contracts/teams x30", 30, probe_contracts,
             "Roster coverage and agreement with the contract snapshot"),
    PlanStep("P10", "contracts", "contracts/players/aggregate", 8, probe_aggregates,
             "Guarantees/options/clauses actually supplied"),
    PlanStep("P11", "injuries", "player_injuries", 3, probe_injuries, "Injury feed shape"),
    PlanStep("P12", "raw", "lineups, plays, v2/stats/advanced", 4, probe_raw_endpoints,
             "Starting lineups only? Play-by-play and tracking fill rates"),
    PlanStep("P13", "errors", "error behaviour", 2, probe_errors,
             "Bad key and bad parameter handling"),
]


def plan_summary(tier: str = "trial") -> dict[str, Any]:
    """The plan and its cost, without making a request."""
    rpm = {"trial": 5, "free": 5, "all_star": 60, "goat": 600, "all_access": 600}[tier]
    total = sum(s.estimated_requests for s in PLAN)
    return {
        "steps": [
            {
                "id": s.id,
                "area": s.area,
                "endpoint": s.endpoint,
                "estimated_requests": s.estimated_requests,
                "allowed_on_tier": _allowed(s, tier),
                "description": s.description,
            }
            for s in PLAN
        ],
        "estimated_requests": total,
        "tier": tier,
        "paced_minutes": round(total / (rpm * 0.8), 1),
        "thresholds": THRESHOLDS,
    }


def _allowed(step: PlanStep, tier: str) -> bool:
    keys = {
        "identity": "players/active",
        "games": "games",
        "standings": "standings",
        "stats": "season_averages",
        "advanced": "season_averages",
        "history": "season_averages",
        "contracts": "contracts",
        "injuries": "player_injuries",
        "raw": "plays",
        "errors": "teams",
    }
    return tier_allows(tier, keys[step.area])


def preflight(
    db: Session,
    provider: BalldontlieProvider,
    *,
    season: Season | None = None,
    budget: int = 200,
) -> dict[str, Any]:
    """Is Pivot ready for the 48-hour clock to start? Makes no request and needs no key.

    The trial window is short and paid-for, so everything that can be checked beforehand is:
    the comparison season's local data must be usable (a finalized season, or fresh in-season
    data) because every probe measures *agreement with it*; the plan must fit the budget; and the
    things that would make probes inconclusive (no staged league-season rosters, an empty contract
    snapshot) are listed as warnings rather than discovered in hour 30.
    """
    from app.canonical.provenance import USABLE
    from app.services.data_provenance import dataset_freshness

    settings = get_settings()
    context = settings.season_context
    comparison = season or context.last_completed_season
    datasets = dataset_freshness(db, comparison)
    blockers: list[str] = []
    warnings: list[str] = []
    if not provider.configured:
        blockers.append("BALLDONTLIE_API_KEY is not set (needed for a live run; replay needs none)")
    for name in ("player_stats", "standings", "games"):
        meta = datasets[name]
        if meta.freshness not in USABLE or meta.needs_attention:
            blockers.append(
                f"Pivot's own {comparison} {name} are {meta.freshness.value}"
                + (f" ({meta.status_reason})" if meta.status_reason else "")
                + ": every probe compares against them, so refresh first"
                + f" (`make refresh DATASETS={name.replace('_', '-')} ARGS='--season {comparison}'`)"
            )
    roster_season = pick_roster_season(db, comparison)
    if roster_season == comparison and context.league_season != comparison:
        warnings.append(
            f"no {context.league_season} rosters are staged: the 'players who changed team' sample "
            "and the contract coverage probe will describe last season's rosters "
            "(`make refresh DATASETS=rosters ARGS='--season "
            f"{context.league_season}'`)"
        )
    linked = db.scalar(
        select(func.count(ProviderEntityId.id)).where(ProviderEntityId.provider == "balldontlie")
    )
    if not linked:
        warnings.append(
            "nothing from BALLDONTLIE has been linked yet, so the adapter has never been called "
            "live. Before the trial, run `make link-balldontlie DRY=1` with a FREE key: it "
            "verifies the key, the API path prefix (/nba/v1 vs /v1) and pagination at no cost"
        )
    # R8.2: judge the roster the harness will actually compare against (the staged league-season
    # one in the summer), not the comparison season's history.
    roster_meta = (
        datasets["rosters"] if roster_season == comparison else dataset_freshness(db, roster_season)["rosters"]
    )
    if roster_meta.needs_attention:
        warnings.append(f"rosters for {roster_season} are {roster_meta.freshness.value}")
    if datasets["contracts"].freshness.value == "missing":
        warnings.append(
            "no contract snapshot is loaded: P9 (coverage) will run, P9b (agreement) will be "
            "inconclusive"
        )
    plan = plan_summary(provider.client.tier)
    needed = plan["estimated_requests"]
    if needed > budget:
        blockers.append(f"the plan needs ~{needed} requests but the budget is {budget}")
    skipped = [s["id"] for s in plan["steps"] if not s["allowed_on_tier"]]
    return {
        "comparison_season": comparison.label,
        "roster_season": roster_season.label,
        "league_season": context.league_season.label,
        "tier": provider.client.tier,
        "estimated_requests": needed,
        "paced_minutes": plan["paced_minutes"],
        "budget": budget,
        "probes_outside_the_plan": skipped,
        "local_data": {
            name: {"freshness": m.freshness.value, "reason": m.status_reason}
            for name, m in datasets.items()
            if name in ("player_stats", "standings", "games", "rosters", "contracts")
        },
        "blockers": blockers,
        "warnings": warnings,
        "ready": not blockers,
    }


def run_plan(
    provider: BalldontlieProvider,
    db: Session,
    out_dir: Path,
    *,
    budget: int = 200,
    only: set[str] | None = None,
    replay_only: bool = False,
    season: Season | None = None,
) -> dict[str, Any]:
    """Execute the plan and write ``findings.json`` + ``report.md`` under ``out_dir``.

    ``season`` is the season statistics are compared on. It defaults to the **last completed**
    season, not the season the tools happen to read: a trial run after the 2026-27 rollover would
    otherwise compare BALLDONTLIE against a season Pivot has barely any data for.
    """
    settings = get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    install_disk_cache(provider.client, out_dir / "responses", replay_only=replay_only)
    comparison = season or settings.season_context.last_completed_season
    ctx = Context(
        provider,
        db,
        comparison,
        out_dir,
        budget,
        replay=replay_only,
        roster_season=pick_roster_season(db, comparison),
    )
    started = datetime.now(UTC)
    for step in PLAN:
        if only and step.id not in only and step.area not in only:
            ctx.findings.append(
                Finding(step.id, step.area, "not_run", "not run: excluded by --only")
            )
            continue
        if not _allowed(step, provider.client.tier):
            ctx.findings.append(
                Finding(step.id, step.area, "restricted",
                        f"skipped: {step.endpoint} is outside the {provider.client.tier} plan "
                        "per the published table")
            )
            continue
        truncated_before = len(provider.client.truncations)
        try:
            ctx.findings.extend(step.run(ctx))
        except BudgetExhaustedError as exc:
            ctx.findings.append(Finding(step.id, step.area, "error", f"budget: {exc}"))
        except BalldontlieError as exc:
            ctx.findings.append(Finding(step.id, step.area, "error", str(exc)))
        for note in provider.client.truncations[truncated_before:]:
            ctx.findings.append(
                Finding(step.id, step.area, "inconclusive", f"incomplete read: {note}")
            )
    counts: dict[str, int] = {}
    for f in ctx.findings:
        counts[f.status] = counts.get(f.status, 0) + 1
    result = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "tier": provider.client.tier,
        "season": comparison.label,
        "roster_season": ctx.live_season.label,
        "requests_made": provider.client.request_count,
        "budget": budget,
        "live": not replay_only,
        "status_counts": counts,
        "findings": [f.__dict__ for f in ctx.findings],
        "thresholds": THRESHOLDS,
    }
    (out_dir / "findings.json").write_text(json.dumps(result, indent=2, default=str))
    (out_dir / "report.md").write_text(render_report(result))
    return result


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# BALLDONTLIE trial evaluation",
        "",
        f"- run: {result['started_at']} -> {result['finished_at']}",
        f"- tier setting: {result['tier']}; requests made: {result['requests_made']} "
        f"of a {result['budget']} budget; "
        + ("LIVE responses" if result["live"] else "REPLAYED from the recorded cache"),
        f"- compared on {result.get('season', '?')} statistics; rosters from "
        f"{result.get('roster_season', '?')}",
        "- results: "
        + ", ".join(f"{n} {k}" for k, n in sorted(result.get("status_counts", {}).items())),
        "- `restricted` = skipped, outside the plan; `error` = blocked or failed; "
        "`inconclusive` = ran but incomplete or undecidable; `not_run` = excluded with --only",
        "",
        "| Probe | Area | Result | Finding |",
        "| --- | --- | --- | --- |",
    ]
    for f in result["findings"]:
        lines.append(f"| {f['probe']} | {f['area']} | **{f['status']}** | {f['summary']} |")
    lines += [
        "",
        "Thresholds were fixed before any live data was seen; `pass`/`fail` here is agreement "
        "with Pivot's existing NBA.com-derived data, not independent truth.",
    ]
    return "\n".join(lines) + "\n"
