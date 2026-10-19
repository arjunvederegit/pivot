"""Data-health service: provider status, sync history, row counts, staleness, and
open quality issues — the backing for /api/v1/data-health and the frontend page."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.canonical.calendar import season_context, today_in_league
from app.canonical.provenance import Freshness
from app.canonical.rosters import resolve_roster_season
from app.config import get_settings
from app.core.cache import get_cache
from app.core.provenance import upstream_for
from app.db.models import (
    Base,
    Contract,
    DataQualityIssue,
    DataSyncRun,
    Game,
    HistoricalTrade,
    LeagueCapParameters,
    ModelVersion,
    Player,
    PlayerImpactEstimate,
    PlayerSeasonStats,
    RosterEntry,
    SeasonCalendar,
    Standing,
    Team,
    TeamNeed,
    TeamSeasonStats,
)
from app.integrations.contracts import get_contract_provider
from app.integrations.nba_api.health import get_provider_health
from app.services.data_provenance import dataset_freshness


def _horizon_phrase(db: Session) -> str:
    """R9.3A: which contract seasons were loaded for the whole league (empty when only the financial season was)."""
    from app.services.financials import horizon

    info = horizon(db, get_settings().cap_league_year)
    if len(info.seasons) < 2:
        return ""
    return f" · contract years {info.seasons[0]} to {info.seasons[-1]} loaded"


def _aware(value: datetime | None) -> datetime | None:
    """Normalize to tz-aware UTC.

    `last_successful_sync` used to serialise naive while `tables[*].last_retrieved_at`
    serialised tz-aware, so the browser parsed the two on different clocks — on the exact
    path the freshness fix touches.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def data_health(db: Session) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(seconds=settings.nba_api_stale_after_seconds)
    identity_cutoff = now - timedelta(seconds=settings.identity_stale_after_seconds)
    ctx = season_context(today_in_league(now), grace_days=settings.season_rollover_grace_days)
    # R8.1: staleness is judged by the calendar, not by a 24 h clock on every table. A completed
    # season's statistics are `finalized`; a season that has not started is `not_yet_available`.
    datasets = dataset_freshness(db, now=now)
    #: table -> the lifecycle-aware dataset that decides whether it needs attention.
    table_dataset = {
        "rosters": "rosters",
        "standings": "standings",
        "player_season_stats": "player_stats",
        "team_season_stats": "team_stats",
        "games": "games",
    }

    # Tables whose freshness is a claim about NBA.com data. `contracts` is deliberately
    # absent: it comes from a user-supplied provider on its own cadence.
    NBA_TABLES = {
        "teams",
        "players",
        "rosters",
        "standings",
        "player_season_stats",
        "team_season_stats",
        "games",
    }

    tables = {}
    # Which providers actually populated each table. The "Current NBA data" card counted
    # rows from every provider while naming only nba_api, so a demo-seeded or CSV-heavy
    # database was described as NBA.com coverage.
    table_providers: dict[str, list[str]] = {}
    nba_retrieved_at: datetime | None = None
    model: type[Base]
    for model, name in [
        (Team, "teams"),
        (Player, "players"),
        (RosterEntry, "rosters"),
        (Standing, "standings"),
        (PlayerSeasonStats, "player_season_stats"),
        (TeamSeasonStats, "team_season_stats"),
        (Game, "games"),
        (Contract, "contracts"),
        (PlayerImpactEstimate, "player_impact_estimates"),
        (TeamNeed, "team_needs"),
        (HistoricalTrade, "historical_trades"),
        (SeasonCalendar, "season_calendar"),
    ]:
        count = db.scalar(select(func.count()).select_from(model)) or 0
        if hasattr(model, "source_provider"):
            table_providers[name] = sorted(
                {
                    p
                    for (p,) in db.execute(
                        select(model.source_provider).distinct()  # type: ignore[attr-defined]
                    )
                    if p
                }
            )
        last_retrieved = None
        stale = None
        if hasattr(model, "source_retrieved_at"):
            last_retrieved = _aware(db.scalar(select(func.max(model.source_retrieved_at))))
            if last_retrieved is not None:
                stale = last_retrieved < (
                    identity_cutoff if name in ("teams", "players") else stale_cutoff
                )
                meta = datasets.get(table_dataset.get(name, ""))
                if meta is not None and meta.freshness is not Freshness.MISSING:
                    stale = meta.freshness in (Freshness.STALE, Freshness.UNKNOWN) or meta.refresh_failing
            if name in NBA_TABLES:
                # Per-source, and filtered to the NBA provider. The audit suggested
                # reusing `tables[*].last_retrieved_at`, but `player_season_stats` also
                # carries CSV-import rows, so the unfiltered maximum is the CSV's
                # timestamp — still wrong, by about 25 hours.
                source_provider = model.source_provider  # type: ignore[attr-defined]
                provider_max = _aware(
                    db.scalar(
                        select(func.max(model.source_retrieved_at)).where(
                            source_provider == "nba_api"
                        )
                    )
                )
                if provider_max is not None and (
                    nba_retrieved_at is None or provider_max > nba_retrieved_at
                ):
                    nba_retrieved_at = provider_max
        tables[name] = {
            "rows": count,
            "last_retrieved_at": last_retrieved.isoformat() if last_retrieved else None,
            "stale": stale,
        }
        table_meta = datasets.get(table_dataset.get(name, ""))
        if table_meta is not None:
            tables[name]["lifecycle"] = table_meta.freshness.value
            tables[name]["lifecycle_reason"] = table_meta.status_reason

    nba_tables_stale = [
        name
        for name, t in tables.items()
        if name in NBA_TABLES and t["stale"] is True and t["rows"]
    ]

    recent_runs = [
        {
            "job": r.job_name,
            "status": r.status,
            "rows": r.rows_written,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "error": f"{r.error_class}: {(r.error_message or '')[:200]}" if r.error_class else None,
        }
        for r in db.scalars(
            select(DataSyncRun).order_by(DataSyncRun.started_at.desc()).limit(25)
        ).all()
    ]
    last_job_finished = _aware(
        db.scalar(
            select(func.max(DataSyncRun.finished_at)).where(DataSyncRun.status == "succeeded")
        )
    )
    # QA-4: this used to be `last_success`, the maximum finish time across *all* jobs, so
    # a local `index_assets` run made six-day-old NBA.com data read as "fresh · updated
    # Jul 27" while the real sync was Jul 21. Freshness is a property of when NBA.com
    # data was retrieved, never of when some job finished.
    last_success = nba_retrieved_at

    ISSUE_PAGE = 50
    open_issue_total = (
        db.scalar(
            select(func.count())
            .select_from(DataQualityIssue)
            .where(DataQualityIssue.resolved_at.is_(None))
        )
        or 0
    )
    open_issue_counts: dict[str, int] = {
        str(row[0]): int(row[1])
        for row in db.execute(
            select(DataQualityIssue.check_name, func.count())
            .where(DataQualityIssue.resolved_at.is_(None))
            .group_by(DataQualityIssue.check_name)
            .order_by(func.count().desc())
        ).all()
    }
    open_issues = [
        {
            "check": i.check_name,
            "severity": i.severity,
            "message": i.message,
            "detected_at": i.detected_at.isoformat() if i.detected_at else None,
        }
        for i in db.scalars(
            select(DataQualityIssue)
            .where(DataQualityIssue.resolved_at.is_(None))
            .order_by(DataQualityIssue.detected_at.desc())
            .limit(ISSUE_PAGE)
        ).all()
    ]

    models = [
        {
            "name": m.model_name,
            "version": m.version,
            "algorithm": m.algorithm,
            "trained_at": m.trained_at.isoformat() if m.trained_at else None,
            "validation": m.validation_metrics,
        }
        for m in db.scalars(select(ModelVersion).where(ModelVersion.is_active)).all()
    ]

    cap_years = [
        row.league_year
        for row in db.scalars(
            select(LeagueCapParameters).order_by(LeagueCapParameters.league_year)
        ).all()
    ]

    contract_provider = get_contract_provider()
    import nba_api as nba_api_pkg

    from app.assets.indexer import coverage as asset_coverage

    assets = asset_coverage(db)

    def latest_run(job: str) -> dict | None:
        for run in recent_runs:
            if run["job"] == job and run["status"] == "succeeded":
                return run
        return None

    # Invariant: NBA data is never reported fresh while any NBA table is stale. The
    # per-table `stale` flags and this headline now derive from the same timestamps, so
    # they cannot disagree.
    needs_attention = sorted(
        name
        for name, meta in datasets.items()
        if name in ("rosters", "standings", "player_stats", "team_stats", "games")
        and (meta.freshness in (Freshness.STALE, Freshness.UNKNOWN) or meta.refresh_failing)
    )
    roster_missing = datasets["rosters"].freshness is Freshness.MISSING
    if last_success is None:
        nba_status = "unavailable"
    elif nba_tables_stale or needs_attention:
        nba_status = "stale"
    elif roster_missing:
        nba_status = "incomplete"
    else:
        nba_status = "fresh"
    # Identity tables (teams, players) are refreshable too; name them when they are the stale part.
    to_refresh = [
        *needs_attention,
        *(t for t in ("teams", "players") if t in nba_tables_stale),
    ]
    refresh_args = ",".join(n.replace("_", "-") for n in to_refresh) or "rosters"

    csv_run = latest_run("import_stats_csv")
    kaggle_run = latest_run("import_kaggle_history")
    assets_run = latest_run("index_assets")
    contracts_run = latest_run("sync_contracts")
    contracts_rows = int(tables.get("contracts", {}).get("rows") or 0)
    # R8.3: how much of the roster the stored contracts price, from the run that wrote them.
    # R9.3A: the coverage is the *financial season's* — a later run for a future season (2030-31 prices 19 players)
    # must not be read as the roster's coverage. Falls back to the latest run when none names the season.
    contracts_details = list(
        db.scalars(
            select(DataSyncRun.detail)
            .where(DataSyncRun.job_name == "sync_contracts", DataSyncRun.status == "succeeded")
            .order_by(DataSyncRun.started_at.desc())
        ).all()
    )
    contracts_detail = next(
        (
            d
            for d in contracts_details
            if isinstance(d, dict) and d.get("season") == get_settings().cap_league_year
        ),
        contracts_details[0] if contracts_details else None,
    )
    contracts_cov = (contracts_detail or {}).get("coverage") if isinstance(contracts_detail, dict) else None
    contracts_share = contracts_cov.get("roster_coverage_share") if isinstance(contracts_cov, dict) else None
    contracts_partial = contracts_share is not None and contracts_share < 1.0
    trades_run = latest_run("import_transactions")
    calendar_sync = latest_run("sync_season_calendar")
    trade_rows = int(tables.get("historical_trades", {}).get("rows") or 0)
    calendar_rows = int(tables.get("season_calendar", {}).get("rows") or 0)

    # The card counts rosters + season stats, and those tables can hold rows from more
    # than one provider (nba_api, the user CSV import, or the demo seed). Name what is
    # actually in them rather than the provider this card was originally written for.
    def _nba_card_source(providers: dict[str, list[str]], nba_api_version: str) -> str:
        present = sorted(
            set(providers.get("rosters", [])) | set(providers.get("player_season_stats", []))
        )
        if present == ["nba_api"] or not present:
            return f"NBA.com via nba_api {nba_api_version}"
        return " + ".join(upstream_for(name) for name in present)

    # Fan-readable source cards: status ∈ fresh|stale|derived|incomplete|unavailable|failed
    source_cards = [
        {
            "key": "current_nba_data",
            "title": "Current NBA data",
            "status": nba_status,
            "season": ctx.describe(),
            "datasets": {name: meta.freshness.value for name, meta in datasets.items()},
            "datasets_needing_attention": needs_attention,
            "last_update": last_success.isoformat() if last_success else None,
            "coverage": f"{tables.get('rosters', {}).get('rows', 0)} roster spots · "
            f"{tables.get('player_season_stats', {}).get('rows', 0)} stat rows",
            "source": _nba_card_source(table_providers, nba_api_pkg.__version__),
            "stale_tables": nba_tables_stale,
            "action": None
            if nba_status in ("fresh",)
            else f"Run `make refresh DATASETS={refresh_args}` to refresh from NBA.com.",
        },
        {
            "key": "contracts",
            "title": "Contracts & salaries",
            # Three distinct states, in the order they must be checked. An empty table
            # with a provider configured is *empty*, not "derived" and not "unavailable":
            # the import ran (or was never run) and produced nothing, which is a
            # different problem from having no provider at all.
            "status": (
                "unavailable"
                if contract_provider is None
                else (
                    "fresh"
                    if contracts_rows > 0 and not contracts_partial
                    else "incomplete"
                )
            ),
            "last_update": contracts_run["finished_at"] if contracts_run else None,
            "coverage": (
                f"{contracts_rows} contracts on file"
                + (
                    f" · {contracts_cov['roster_players_with_salary_for_cap_league_year']}"
                    f"/{contracts_cov['roster_players_total']} rostered players priced"
                    if contracts_partial and isinstance(contracts_cov, dict)
                    else ""
                )
                + _horizon_phrase(db)
                if contracts_rows
                else (
                    "no contracts imported yet"
                    if contract_provider is not None
                    else "no contract provider configured"
                )
            ),
            "source": contract_provider.name if contract_provider else "not configured",
            "action": (
                "Contract coverage is partial: payroll is shown as a lower bound and cap, tax "
                "and apron position stay withheld until every rostered player is priced."
                if contract_provider is not None and contracts_rows > 0 and contracts_partial
                else None
            )
            if contract_provider is not None and contracts_rows > 0
            else (
                f"{contracts_rows} contracts are on file but no contract provider is configured, "
                "so salary rules treat salary data as unconfigured. To use the BALLDONTLIE-backed "
                "rows set CONTRACT_DATA_PROVIDER=balldontlie (see docs/r8-3/owner-next-steps.md)."
                if contracts_rows > 0
                else None
            )
            or (
                "A contract provider is configured but no rows were imported. Check the "
                "snapshot file and run `make import-contracts`."
                if contract_provider is not None
                else "Download the Basketball-Reference contracts page to "
                "data/imports/contracts/players.html, set "
                "CONTRACT_DATA_PROVIDER=bbref_snapshot, then run `make sync-data`. "
                "Salary rules stay unavailable until then."
            ),
        },
        {
            "key": "player_photos",
            "title": "Player photos",
            "status": "derived"
            if assets.get("rostered_players_with_photo", 0) > 0
            else "unavailable",
            "last_update": assets_run["finished_at"] if assets_run else None,
            "coverage": f"{round(100 * assets.get('player_photo_coverage', 0))}% of rostered players",
            "source": "local image dataset (name→ID resolved; unmatched kept for review)",
            "action": None
            if assets.get("rostered_players_with_photo", 0) > 0
            else "Place image folders at ./nbaplayerimages and run `make index-assets`.",
        },
        {
            "key": "team_logos",
            "title": "Team logos",
            "status": "derived" if assets.get("teams_with_logo", 0) >= 30 else "incomplete",
            "last_update": assets_run["finished_at"] if assets_run else None,
            "coverage": f"{assets.get('teams_with_logo', 0)}/30 teams",
            "source": "local logo dataset (CC0)",
            "action": None
            if assets.get("teams_with_logo", 0) >= 30
            else "Run `make index-assets`.",
        },
        {
            "key": "historical_database",
            "title": "Historical database",
            "status": "derived" if kaggle_run else "unavailable",
            "last_update": kaggle_run["finished_at"] if kaggle_run else None,
            "coverage": "player bio/draft enrichment"
            + (" + current-season totals CSV" if csv_run else ""),
            "source": "Kaggle wyattowalsh/basketball + user CSV import",
            "action": None if kaggle_run else "Run `make import-kaggle` (multi-GB download).",
        },
        {
            # R7. The page's first sentence is "every screen traces to one of these
            # sources", and since R6 the Precedent panel has traced to one that was not
            # listed. A provenance surface that omits a source is worse than one that
            # admits a gap.
            "key": "historical_trades",
            "title": "Completed trades",
            "status": (
                "derived"
                if trade_rows > 0 and calendar_rows > 0
                # Trades without a season calendar still rank, but each one's feature
                # season falls back to its calendar month — which mis-describes
                # draft-night, preseason and November-2020 trades. That is a partial
                # source, not a working one.
                else ("incomplete" if trade_rows > 0 else "unavailable")
            ),
            # Whichever half of this source was refreshed last. The two are ingested by
            # different commands and either can be the stale one.
            "last_update": max(
                (r["finished_at"] for r in (trades_run, calendar_sync) if r and r["finished_at"]),
                default=None,
            ),
            "coverage": (
                f"{trade_rows} trades"
                + (
                    f" · {calendar_rows} season calendars"
                    if calendar_rows
                    else " · no season calendar"
                )
                if trade_rows
                else "no completed trades imported"
            ),
            "source": "Basketball-Reference season transaction pages (local snapshots)",
            "action": (
                None
                if trade_rows > 0 and calendar_rows > 0
                else (
                    "Run `make sync-season-calendar`; without it each trade's feature "
                    "season is guessed from its calendar month."
                    if trade_rows > 0
                    else "Run `make fetch-transactions FROM=2017 TO=2026` then "
                    "`make import-transactions`. Comparable trades stay unavailable "
                    "until then."
                )
            ),
        },
        {
            "key": "models",
            "title": "Models & evaluation",
            "status": "fresh" if models else "unavailable",
            "last_update": models[0]["trained_at"] if models else None,
            "coverage": f"{len(models)} active models",
            "source": "trained locally on ingested data (validation metrics below)",
            "action": None if models else "Run `make train && make score`.",
        },
    ]

    return {
        "generated_at": now.isoformat(),
        "current_season": settings.current_season,
        # R8.2: `current_season` stays the statistics season; the roster season is stated beside it.
        "roster_season": resolve_roster_season(db),
        "season_context": ctx.describe(),
        "cap_league_year": settings.cap_league_year,
        "source_cards": source_cards,
        "asset_coverage": assets,
        "providers": {
            "nba_api": {
                "configured": True,
                "package_version": nba_api_pkg.__version__,
                "upstream": "NBA.com (stats.nba.com; live cdn disabled by default)",
                "endpoints": get_provider_health().snapshot(),
            },
            "contracts": {
                "configured": contract_provider is not None,
                "provider": contract_provider.name if contract_provider else None,
                "note": None
                if contract_provider
                else "No contract provider configured — salary features are unavailable "
                "(nba_api does not supply contracts).",
            },
            "injuries": {
                "configured": bool(settings.injury_data_provider),
                "note": None
                if settings.injury_data_provider
                else "No injury provider configured — injury status is unavailable and "
                "never fabricated; availability uses historical games played.",
            },
            "live_games": {
                "enabled": settings.live_data_enabled,
                "note": "Live scoreboard polling is disabled (offseason / cdn.nba.com "
                "unreachable states are surfaced, not hidden).",
            },
        },
        "cache_backend": get_cache().backend,
        "tables": tables,
        "cap_parameter_years": cap_years,
        "last_successful_sync": last_success.isoformat() if last_success else None,
        "last_job_finished_at": last_job_finished.isoformat() if last_job_finished else None,
        "nba_tables_stale": nba_tables_stale,
        "recent_sync_runs": recent_runs,
        "open_quality_issues": open_issues,
        # The list is capped, so without a total the real backlog is unknowable from the
        # API — 562 open rows rendered as 50 with no indication that 512 were missing.
        "open_quality_issue_total": open_issue_total,
        "open_quality_issue_counts": open_issue_counts,
        "open_quality_issues_truncated": open_issue_total > len(open_issues),
        "active_models": models,
    }
