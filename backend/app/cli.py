"""Pivot operational CLI.

Usage: python -m app.cli <command>

Commands
  sync-all         Full provider-backed refresh (teams, players, rosters, standings,
                   stats, games, contracts) + data quality validation
  sync-<job>       Run one job (teams|players|rosters|standings|player-stats|team-stats|
                   games|season-calendar|contracts)
  sync-corpus-stats
                   Ingest player season stats and standings for every season in
                   CORPUS_SEASONS, plus the season calendar. This widens what the
                   historical-trade corpus can be described by; it does NOT widen the
                   modelling window, which stays HISTORY_SEASONS.
  seed-config      Load cap-parameter YAML files into league_cap_parameters
  build-features   Build modeling features from ingested data
  train            Train impact model + archetypes; persist model versions
  score            Score current players, compute availability and team needs
  backtest         R9.1 rolling-origin backtest: backtest <development|holdout> [--target T|all]
                   [--out DIR] [--eval-min-target-minutes N] [--acknowledge-reread]. A holdout run
                   loads and verifies the development run in --out. Read-only on the database;
                   writes artifacts under models/experiments/.
  train-candidate  R9.1 candidate-only training: train-candidate --model M --target T
                   [--origin SEASON] [--experiment DIR]. Writes a candidate model_versions row and
                   candidate_estimates only; refuses to change anything else.
  candidates       List candidate and rejected model versions (read-only).
  validate-data    Run data-quality checks
  index-assets     Index local player photos / team logos into the media manifest
  import-stats-csv <path> [season]  Import the user-supplied season-totals CSV (default season 2025-26; default
                   data/imports/nba_player_stats_2026.csv)
  import-kaggle    Import historical enrichment from the Kaggle basketball dataset
  seed-demo        Populate a DEDICATED database with the synthetic demo league used by
                   the end-to-end suite. Refuses to run where nba_api rows exist.
  import-draft-picks [path]
                   Import pick ownership from a local RealGM future-drafts snapshot
                   (default data/imports/draft_picks/realgm_future_drafts.html). Only
                   unconditional transfers become verified ownership; swaps, protected
                   and conditional picks are recorded with their source sentence and
                   filed as data-quality warnings.
  pick-ownership [year] [round]
                   Report who verifiably controls each team's own pick for a draft.
  fetch-transactions [first_end_year] [last_end_year]
                   Fetch Basketball-Reference season transaction pages into
                   data/imports/transactions/ (gitignored). One request per season,
                   3.5 s apart, honouring the source's published Crawl-delay. Existing
                   files are kept unless --force is passed.
  import-transactions
                   Parse the local transaction snapshots into historical_trades /
                   historical_trade_assets. Nothing is fuzzy-matched: an unresolvable
                   name is recorded as unresolved and filed as a data-quality warning.
  transaction-coverage
                   Report what the imported corpus contains without importing anything.
  acquisition-validation
                   Run the need-driven acquisition validation battery over the whole
                   league: need-filter differentiation, same-need versus cross-need
                   agreement, the shuffled-need null, and a team-type breakdown.
  lineup-availability [season]
                   Measure whether NBA.com lineup data could support a lineup-aware fit
                   model: group counts, minutes distributions and the implied standard
                   error of a net-rating estimate at each group size. Network required;
                   nothing is stored.
  adversarial-validation
                   Run the adversarial trade battery against the ingested league: does
                   the product refuse well? Empty trades score exactly neutral, verified-
                   illegal trades carry no decision score and name their rule, giving away
                   the best three never reads as a gain, an unpriced player is disclosed
                   rather than defaulted, and an impossible trade is refused at
                   construction. Exits non-zero on any failure.
  comparable-validation
                   Run the comparable-trade validation battery: perturbation stability,
                   scale-form and distance-form sensitivity, single-dimension and
                   shuffled-feature nulls, leave-one-dimension-out, archetype recovery,
                   direction confusion, era structure and season concentration. Exits
                   non-zero when a stated threshold fails.
  contract-coverage
                   Report ROSTER-side contract coverage without importing anything:
                   how many rostered players have a salary for the cap league year,
                   and how many teams therefore have a computable payroll.
  season-status    Where the NBA calendar is (league season, phase, which season the tools read),
                   what is stored for the league's own season, and whether the data needed to
                   roll the tools over to it is in place (read-only).
  data-status      Report freshness, completeness and provider of every dataset the
                   intelligence tools read, plus the provider registry (read-only).
  refresh <datasets> [--season 2026-27] [--season-type regular|preseason]
                   Refresh selected datasets (comma-separated: rosters, standings,
                   player-stats, team-stats, games, season-calendar, contracts, plus the identity
                   tables teams and players) and print
                   the resulting freshness. A snapshot a provider answers with but that
                   cannot be the league (empty, a fraction of it) is rejected and the
                   stored data is left untouched. Network required.
  link-balldontlie [--dry-run] [--active-only]
                   Fetch BALLDONTLIE's teams and players (the free-tier /players list,
                   ~50 pages; --active-only uses /players/active, which needs a paid plan)
                   and link their ids to Pivot's players and teams (provider_entity_ids).
                   Ambiguous or unmatched players are reported and left unlinked. Needs
                   BALLDONTLIE_API_KEY.
  refresh-balldontlie <identities|contracts|injuries|stats|all, or a comma list> [--season 2026-27]
                   [--stat-season 2025-26] [--season-type regular|playoffs] [--groups general/base,...]
                   [--cache DIR] [--replay] [--dry-run]
                   Fetch BALLDONTLIE data and write it through the canonical tables, with provenance
                   and a data_sync_runs row per job. Idempotent and non-destructive; unmapped players are
                   quarantined in the run detail. --cache DIR reads/writes recorded responses (re-runs
                   cost no requests); --replay serves only recorded responses; --dry-run rolls back.
                   `identities` must have run once before `contracts`/`injuries`/`stats`.
  balldontlie-plan [tier]
                   Print the GOAT trial evaluation plan, its request cost and pacing.
                   Makes no request.
  balldontlie-preflight [--budget N] [--season 2025-26]
                   Is Pivot ready for the 48-hour trial clock? Checks the comparison season's local
                   data, the plan against the budget and the staged rosters. No request, no key.
  balldontlie-evaluate [--budget N] [--only P5,P9] [--replay DIR | --resume DIR] [--season 2025-26] [--force]
                   Run the trial evaluation against Pivot's own data and write evidence to
                   data/imports/balldontlie/ (gitignored). --replay re-analyses recorded
                   responses with no requests. Needs BALLDONTLIE_API_KEY unless replaying.
  purge-fixtures [--apply]
                   List (or with --apply, delete) scenarios, trade proposals and
                   comparison sets whose names look like automated-test leftovers.
                   Dry run by default.
"""

import json
import os
import sys
from collections.abc import Callable
from datetime import date, datetime

import yaml

from app.canonical.rosters import resolve_roster_season
from app.config import CAP_RULES_DIR, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.base import SessionLocal

logger = get_logger(__name__)

#: Commands that reach a third party. `ROSTERLAB_OFFLINE=1` refuses them. The variable
#: name predates the rename to Pivot and is kept because the test suite and CI set it.
#:
#: The test suite sets it, and it has to: `test_every_documented_command_is_reachable`
#: executes every command named in this docstring, which for these two means a real
#: request to Basketball-Reference or NBA.com on every `pytest` run — ten pages at a
#: 3.5-second crawl delay in the first case. A test suite is not a reason to make traffic
#: on someone else's servers, and a CI runner is not a reason either.
NETWORK_COMMANDS = frozenset(
    {
        "fetch-transactions",
        "lineup-availability",
        "refresh",
        "link-balldontlie",
        "balldontlie-evaluate",
        "refresh-balldontlie",
    }
)


def offline() -> bool:
    return os.environ.get("ROSTERLAB_OFFLINE", "").strip() not in ("", "0", "false", "False")


def seed_config() -> None:
    from sqlalchemy import select

    from app.db.models import LeagueCapParameters

    with SessionLocal() as db:
        for path in sorted(CAP_RULES_DIR.glob("*.yaml")):
            doc = yaml.safe_load(path.read_text())
            params = doc["parameters"]
            values = {
                "league_year": doc["league_year"],
                "salary_cap": params["salary_cap"],
                "luxury_tax": params["luxury_tax"],
                "first_apron": params["first_apron"],
                "second_apron": params["second_apron"],
                "minimum_team_salary": params["minimum_team_salary"],
                "effective_date": date.fromisoformat(str(doc["effective_date"])),
                "source_name": doc["source_name"],
                "source_url": doc.get("source_url"),
                "verified_at": datetime.fromisoformat(str(doc["verified_at"])),
                "notes": doc.get("notes"),
                "extras": doc.get("extras", {}),
            }
            row = db.scalar(
                select(LeagueCapParameters).where(
                    LeagueCapParameters.league_year == doc["league_year"]
                )
            )
            if row is None:
                db.add(LeagueCapParameters(**values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            print(f"loaded cap parameters {doc['league_year']} from {path.name}")
        db.commit()


REFRESH_DATASETS = {
    "teams": "sync-teams",
    "players": "sync-players",
    "rosters": "sync-rosters",
    "standings": "sync-standings",
    "player-stats": "sync-player-stats",
    "team-stats": "sync-team-stats",
    "games": "sync-games",
    "season-calendar": "sync-season-calendar",
    "contracts": "sync-contracts",
}


def _refresh(args: list[str], single_jobs: dict[str, Callable[..., int]]) -> int:
    """Refresh the named datasets in the given order; report each result and the freshness."""
    def option(flag: str) -> str | None:
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None

    season_arg, type_arg = option("--season"), option("--season-type")
    values = {option_value for option_value in (season_arg, type_arg) if option_value}
    names = [
        n.strip()
        for a in args
        if not a.startswith("--") and a not in values
        for n in a.split(",")
        if n.strip()
    ]
    unknown = [n for n in names if n not in REFRESH_DATASETS]
    if not names or unknown:
        print(
            json.dumps(
                {
                    "error": "name at least one dataset" if not names else f"unknown: {unknown}",
                    "datasets": sorted(REFRESH_DATASETS),
                },
                indent=2,
            )
        )
        return 2
    from app.canonical.seasons import InvalidSeasonError, Season, SeasonType
    from app.services.data_provenance import dataset_freshness

    try:
        season = Season.parse(season_arg).label if season_arg else None
        season_type = SeasonType(type_arg) if type_arg else SeasonType.REGULAR
    except (InvalidSeasonError, ValueError) as exc:
        print(json.dumps({"error": f"bad --season/--season-type: {exc}"}, indent=2))
        return 2

    def call(job_name: str, job: Callable[..., int], db):
        """Pass the optional scope only to the jobs that take it."""
        if job_name in ("sync-player-stats", "sync-team-stats"):
            seasons = [season] if season else None
            return job(db, seasons, season_type=season_type)
        if job_name == "sync-rosters" and "--roster-only" in args:
            # Memberships + brand-new identities only; existing players' bio fields stay as stored.
            return job(db, season, update_existing_bio=False)
        if job_name in ("sync-rosters", "sync-standings", "sync-games") and season:
            return job(db, season)
        return job(db)

    results: dict[str, dict] = {}
    with SessionLocal() as db:
        for name in names:
            job = single_jobs[REFRESH_DATASETS[name]]
            try:
                results[name] = {
                    "status": "succeeded",
                    "rows": call(REFRESH_DATASETS[name], job, db),
                }
            except Exception as exc:  # noqa: BLE001 - reported per dataset, never swallowed
                results[name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        freshness = {
            n: m.model_dump(mode="json")
            for n, m in dataset_freshness(db, season).items()
            if n in {d.replace("-", "_") for d in names} | {"player_stats", "team_stats"}
        }
        report: dict = {"results": results, "freshness": freshness}
        if "rosters" in names:
            from app.canonical.tool_seasons import roster_verification

            # R8.2: after a roster refresh, say whether the snapshot is whole.
            report["roster_verification"] = roster_verification(
                db, season or get_settings().roster_season_preferred
            )
    print(json.dumps(report, indent=2, default=str))
    return 0 if all(r["status"] == "succeeded" for r in results.values()) else 2


def _link_balldontlie(dry_run: bool, active_only: bool = False) -> int:
    from app.integrations.balldontlie.crosswalk import crosswalk_players, crosswalk_teams
    from app.integrations.balldontlie.provider import BalldontlieProvider

    provider = BalldontlieProvider.from_settings()
    if not provider.configured:
        print(json.dumps({"error": "BALLDONTLIE_API_KEY is not set"}, indent=2))
        return 2
    out: dict = {"dry_run": dry_run}
    with SessionLocal() as db:
        teams = provider.teams()
        players = provider.active_players() if active_only else provider.players()
        for label, result in (("teams", teams), ("players", players)):
            out[label + "_call"] = {
                "status": result.status.value,
                "records": len(result.records),
                "warnings": list(result.warnings)[:5],
                "error": result.error,
            }
        if not (teams.has_answer and players.has_answer):
            print(json.dumps(out, indent=2, default=str))
            return 2
        out["teams"] = crosswalk_teams(db, list(teams.records)).summary()
        report = crosswalk_players(
            db, list(players.records), season=resolve_roster_season(db)
        )
        out["players"] = report.summary() | {
            "ambiguous_examples": report.ambiguous[:10],
            "unresolved_examples": report.unresolved[:10],
        }
        if dry_run:
            db.rollback()
        else:
            db.commit()
    print(json.dumps(out, indent=2, default=str))
    return 0


def _refresh_balldontlie(args: list[str]) -> int:
    from pathlib import Path

    from app.canonical.seasons import Season, SeasonType
    from app.ingestion import balldontlie_jobs as jobs_bdl
    from app.integrations.balldontlie.evaluation import install_disk_cache
    from app.integrations.balldontlie.provider import BalldontlieProvider

    def option(name: str) -> str | None:
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None

    what = args[0] if args and not args[0].startswith("--") else ""
    steps = (
        ["identities", "contracts", "injuries", "stats"]
        if what == "all"
        else [w for w in what.split(",") if w]
    )
    if not what or any(s not in ("identities", "contracts", "injuries", "stats") for s in steps):
        print(json.dumps({"error": "usage: refresh-balldontlie <identities|contracts|injuries|stats|all>"}))
        return 2
    replay = "--replay" in args
    provider = BalldontlieProvider.from_settings()
    if not replay and not provider.configured:
        print(json.dumps({"error": "BALLDONTLIE_API_KEY is not set; nothing was requested"}, indent=2))
        return 2
    cache = option("--cache")
    if cache or replay:
        install_disk_cache(
            provider.client, Path(cache or jobs_bdl.default_cache_dir()), replay_only=replay
        )
    settings = get_settings()
    contract_season = option("--season") or settings.cap_league_year
    stat_season = option("--stat-season") or settings.season_context.last_completed_season.label
    season_type = SeasonType(option("--season-type") or "regular")
    groups = (option("--groups") or ",".join(jobs_bdl.DEFAULT_STAT_GROUPS)).split(",")
    out: dict = {"dry_run": "--dry-run" in args, "replay": replay}
    status = 0
    with SessionLocal() as db:
        for step in steps:
            try:
                if step == "identities":
                    out[step] = jobs_bdl.sync_identities(db, provider)
                elif step == "contracts":
                    out[step] = jobs_bdl.sync_contracts_from_balldontlie(
                        db, provider, Season.parse(contract_season)
                    )
                elif step == "injuries":
                    out[step] = jobs_bdl.sync_injuries_from_balldontlie(db, provider)
                else:
                    out[step] = jobs_bdl.sync_stats_from_balldontlie(
                        db, provider, Season.parse(stat_season), season_type, groups
                    )
            except Exception as exc:  # noqa: BLE001 — recorded in data_sync_runs; reported here
                out[step] = {"error": f"{type(exc).__name__}: {exc}"}
                status = 2
                break
        if "--dry-run" in args:
            db.rollback()
    out["requests_made"] = provider.client.request_count
    print(json.dumps(out, indent=2, default=str))
    return status


def _balldontlie_evaluate(args: list[str]) -> int:
    from pathlib import Path

    from app.config import BACKEND_DIR
    from app.integrations.balldontlie.evaluation import run_plan
    from app.integrations.balldontlie.provider import BalldontlieProvider

    def option(name: str) -> str | None:
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None

    replay = option("--replay")
    resume = option("--resume")  # live, but responses already recorded in DIR cost nothing
    provider = BalldontlieProvider.from_settings()
    from app.canonical.seasons import Season

    season_arg = option("--season")
    season = Season.parse(season_arg) if season_arg else None
    if not replay and not provider.configured:
        print(json.dumps({"error": "BALLDONTLIE_API_KEY is not set; nothing was requested"}, indent=2))
        return 2
    only_arg = option("--only")
    only = set(only_arg.split(",")) if only_arg else None
    out_dir = (
        Path(replay or resume or "")
        if (replay or resume)
        else BACKEND_DIR.parent
        / "data"
        / "imports"
        / "balldontlie"
        / f"eval-{datetime.now().strftime('%Y%m%d-%H%M')}"
    )
    with SessionLocal() as db:
        if not replay:
            from app.integrations.balldontlie.evaluation import preflight

            gate = preflight(db, provider, season=season, budget=int(option("--budget") or 200))
            if not gate["ready"] and "--force" not in args:
                print(json.dumps({"refused": "preflight failed; nothing was requested", **gate}, indent=2))
                return 2
        result = run_plan(
            provider,
            db,
            out_dir,
            budget=int(option("--budget") or 200),
            only=only,
            replay_only=bool(replay),
            season=season,
        )
    print((out_dir / "report.md").read_text())
    print(f"evidence written to {out_dir}")
    failed = [f["probe"] for f in result["findings"] if f["status"] in ("fail", "error")]
    return 2 if failed else 0


def _modeling_command(command: str, args: list[str]) -> int:
    """R9.1 evaluation and candidate-training commands (see ``app.modeling``).

    Every refusal is a JSON object on stdout and exit code 2, like the other operator commands."""
    from pathlib import Path

    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.config import BACKEND_DIR
    from app.modeling import registry
    from app.modeling.backtest import InsufficientDataError, NonFiniteForecastError
    from app.modeling.experiment import (
        DEFAULT_PROTOCOL,
        R92_PROTOCOL,
        HoldoutPrerequisiteError,
        HoldoutReadRefused,
        Protocol,
        run_stage,
    )
    from app.modeling.panel import PanelValidationError, PlayerSeasonPanel
    from app.modeling.predictors import PREDICTORS
    from app.modeling.targets import TARGETS

    def refuse(error: str, message: str, **extra: object) -> int:
        print(json.dumps({"error": error, "message": message, **extra}, indent=2, default=str))
        return 2

    def option(name: str) -> str | None:
        if name not in args:
            return None
        i = args.index(name) + 1
        # A value is never another flag: `--model --target x` is a missing value, not a model called "--target".
        return args[i] if i < len(args) and not args[i].startswith("--") else None

    migration_hint = "run `make migrate` on a COPY of the database first"
    try:
        with SessionLocal() as db:
            if command == "candidates":
                print(json.dumps(registry.list_candidates(db), indent=2, default=str))
                return 0
            if command == "train-candidate":
                model, target = option("--model"), option("--target")
                if not model or not target:
                    return refuse("usage", "train-candidate --model M --target T [--origin SEASON] [--experiment DIR]")
                if model not in PREDICTORS:
                    return refuse("CandidateError", f"unknown model {model!r}; registered: {sorted(PREDICTORS)}")
                if target not in TARGETS:
                    return refuse("CandidateError", f"unknown target {target!r}; registered: {sorted(TARGETS)}")
                experiment = option("--experiment")
                origin = option("--origin")
                if origin is None:
                    origin = PlayerSeasonPanel.from_db(db).seasons[-1]
                record = registry.train_candidate(
                    db, model, target, origin, experiment_dir=Path(experiment) if experiment else None
                )
                print(json.dumps(record.__dict__, indent=2, default=str))
                return 0

            stage = args[0] if args and not args[0].startswith("--") else None
            if stage not in ("development", "holdout"):
                return refuse("usage", "backtest <development|holdout> [--target T|all] [--out DIR] [--eval-min-target-minutes N]")
            wanted = option("--target") or "impact_proxy"
            if wanted != "all" and wanted not in TARGETS:
                return refuse("CandidateError", f"unknown target {wanted!r}; registered: {sorted(TARGETS)}")
            eval_raw = option("--eval-min-target-minutes")
            try:
                eval_minutes = float(eval_raw) if eval_raw is not None else None
            except ValueError:
                return refuse("usage", f"--eval-min-target-minutes needs a number, got {eval_raw!r}")
            r92 = option("--protocol") == "r9.2"
            exposure_raw = option("--eval-min-target-exposure")
            try:
                eval_exposure = float(exposure_raw) if exposure_raw is not None else None
            except ValueError:
                return refuse("usage", f"--eval-min-target-exposure needs a number, got {exposure_raw!r}")
            if r92:
                from app.player_intelligence.panel import build_skill_panel

                panel = build_skill_panel(db)
            else:
                panel = PlayerSeasonPanel.from_db(db)
            standings = registry.league_standings(db)
    except PanelValidationError as exc:
        return refuse("PanelValidationError", str(exc))
    except (registry.CandidateError, registry.ProtectedStateViolation, InsufficientDataError, NonFiniteForecastError) as exc:
        return refuse(type(exc).__name__, str(exc))
    except (OperationalError, ProgrammingError) as exc:
        return refuse("DatabaseError", str(exc)[:300], hint=migration_hint)

    out = Path(option("--out") or BACKEND_DIR.parent / "models" / "experiments")
    if r92:
        # R9.1's holdout reads still count under R9.2: a target read there is a RE-read here.
        prior = Path(option("--prior-ledger") or BACKEND_DIR.parent / "docs" / "r9-1" / "results" / "holdout_ledger.json")
        protocol: Protocol = Protocol(preregistration=R92_PROTOCOL.preregistration, prior_ledgers=(prior,))
    else:
        protocol = DEFAULT_PROTOCOL
    targets = list(TARGETS) if wanted == "all" else [wanted]
    summary = {}
    for name in targets:
        development_dir = None
        if stage == "holdout":
            runs = sorted(out.glob(f"exp-development-{name}-*"))
            if len(runs) != 1:
                return refuse("HoldoutPrerequisiteError", f"a holdout run needs exactly one development run of {name} in {out}; found {len(runs)}")
            development_dir = runs[0]
        try:
            outcome = run_stage(
                panel, standings, stage, name, out,
                development_dir=development_dir, eval_min_target_minutes=eval_minutes,
                eval_min_target_exposure=eval_exposure, protocol=protocol,
                acknowledge_reread="--acknowledge-reread" in args,
                # The primary target is audited at every development origin; the rest at the last one.
                audit_origins=None if (name == "impact_proxy" or r92) else 1,
            )
        except (HoldoutReadRefused, HoldoutPrerequisiteError, InsufficientDataError, NonFiniteForecastError) as exc:
            return refuse(type(exc).__name__, str(exc))
        summary[name] = {
            "directory": str(outcome.directory),
            "family": outcome.decisions.get("family"),
            "reference_baseline": outcome.decisions.get("reference_baseline")
            or outcome.decisions.get("reference_baseline_from_development"),
            "advanced": outcome.decisions.get("advanced"),
            "confirmed": outcome.decisions.get("confirmed"),
        }
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main() -> None:
    configure_logging()
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    command = sys.argv[1]
    if command in NETWORK_COMMANDS and offline():
        print(
            json.dumps(
                {
                    "refused": command,
                    "reason": "ROSTERLAB_OFFLINE is set, and this command reaches a "
                    "third-party service",
                },
                indent=2,
            )
        )
        sys.exit(3)

    from app.ingestion import jobs

    single_jobs: dict[str, Callable[..., int]] = {
        "sync-teams": jobs.sync_teams,
        "sync-players": jobs.sync_players,
        "sync-rosters": jobs.sync_rosters,
        "sync-standings": jobs.sync_standings,
        "sync-player-stats": jobs.sync_player_stats,
        "sync-team-stats": jobs.sync_team_stats,
        "sync-games": jobs.sync_games,
        "sync-season-calendar": jobs.sync_season_calendar,
        "sync-contracts": jobs.sync_contracts,
    }

    if command == "seed-config":
        seed_config()
    elif command == "contract-coverage":
        from app.ingestion.contract_coverage import (
            contract_coverage,
            roster_side_unmatched,
            summarize,
        )

        settings = get_settings()
        with SessionLocal() as db:
            roster_season = resolve_roster_season(db)
            coverage = contract_coverage(db, roster_season, settings.cap_league_year)
            uncovered = roster_side_unmatched(db, roster_season, settings.cap_league_year)
        print(json.dumps({**coverage, "roster_players_without_salary": uncovered}, indent=2, default=str))
        print("\n" + summarize(coverage, uncovered))
    elif command == "data-status":
        from app.services.data_provenance import dataset_freshness, provider_registry

        with SessionLocal() as db:
            datasets = dataset_freshness(db)
            registry = provider_registry(db)
        print(
            json.dumps(
                {
                    "datasets": {n: m.model_dump(mode="json") for n, m in datasets.items()},
                    "providers": registry,
                },
                indent=2,
                default=str,
            )
        )
    elif command == "season-status":
        from app.canonical.tool_seasons import roster_verification, tool_seasons
        from app.services.data_provenance import dataset_freshness, rollover_readiness

        with SessionLocal() as db:
            readiness = rollover_readiness(db)
            current = dataset_freshness(db)
            league_label = get_settings().season_context.league_season.label
            league = (
                dataset_freshness(db, league_label)
                if league_label not in (get_settings().current_season, readiness["tools_read_roster_season"])
                else {}
            )
            # R8.2: which season each kind of information is read from, and is the roster whole?
            season_blocks = tool_seasons(db)
            rosters = roster_verification(db)
        summary = {
            "seasons": season_blocks,
            "roster_verification": rosters,
            "readiness": readiness,
            "tools_read": {n: m.freshness.value for n, m in current.items()},
            "league_season": {n: m.freshness.value for n, m in league.items()},
        }
        print(json.dumps(summary, indent=2, default=str))
    elif command == "refresh":
        sys.exit(_refresh(sys.argv[2:], single_jobs))
    elif command == "link-balldontlie":
        sys.exit(_link_balldontlie("--dry-run" in sys.argv[2:], "--active-only" in sys.argv[2:]))
    elif command == "refresh-balldontlie":
        sys.exit(_refresh_balldontlie(sys.argv[2:]))
    elif command == "balldontlie-plan":
        from app.integrations.balldontlie.evaluation import plan_summary

        tier = sys.argv[2] if len(sys.argv) > 2 else get_settings().balldontlie_tier
        print(json.dumps(plan_summary(tier), indent=2))
    elif command == "balldontlie-preflight":
        from app.canonical.seasons import Season
        from app.integrations.balldontlie.evaluation import preflight
        from app.integrations.balldontlie.provider import BalldontlieProvider

        def opt(name: str) -> str | None:
            a = sys.argv[2:]
            return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else None

        provider = BalldontlieProvider.from_settings()
        season_opt = opt("--season")
        with SessionLocal() as db:
            gate = preflight(
                db,
                provider,
                season=Season.parse(season_opt) if season_opt else None,
                budget=int(opt("--budget") or 200),
            )
        print(json.dumps(gate, indent=2, default=str))
        sys.exit(0 if gate["ready"] else 2)
    elif command == "balldontlie-evaluate":
        sys.exit(_balldontlie_evaluate(sys.argv[2:]))
    elif command == "import-draft-picks":
        from app.ingestion.draft_picks import import_draft_picks

        source = sys.argv[2] if len(sys.argv) > 2 else None
        with SessionLocal() as db:
            summary = import_draft_picks(db, source)
        print(json.dumps(summary, indent=2, default=str))
        if summary.get("error"):
            sys.exit(2)
    elif command == "fetch-transactions":
        from app.ingestion.transactions.fetch import FetchRefused, fetch_seasons

        args = [a for a in sys.argv[2:] if not a.startswith("--")]
        first = int(args[0]) if args else 2017
        last = int(args[1]) if len(args) > 1 else date.today().year
        try:
            summary = fetch_seasons(first, last, force="--force" in sys.argv[2:])
        except FetchRefused as exc:
            print(f"fetch-transactions refused: {exc}")
            sys.exit(2)
        print(json.dumps(summary, indent=2, default=str))
        if summary["failed"]:
            sys.exit(2)
    elif command == "import-transactions":
        from app.ingestion.transactions.importer import import_transactions

        source = sys.argv[2] if len(sys.argv) > 2 else None
        with SessionLocal() as db:
            summary = import_transactions(db, source)
        print(json.dumps(summary, indent=2, default=str))
        if summary.get("error"):
            sys.exit(2)
    elif command == "lineup-availability":
        from app.analytics.lineup_availability import measure

        season = sys.argv[2] if len(sys.argv) > 2 else "2024-25"
        try:
            print(json.dumps(measure(season), indent=2, default=str))
        except Exception as exc:  # network or upstream failure, reported not swallowed
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)[:300]}, indent=2))
            sys.exit(2)
    elif command == "acquisition-validation":
        from app.services.acquisition_validation import run_battery as acquisition_battery

        with SessionLocal() as db:
            report = acquisition_battery(db)
        print(json.dumps(report, indent=2, default=str))
        if report["failed"]:
            print(f"\nFAILED: {', '.join(report['failed'])}")
            sys.exit(2)
    elif command == "comparable-validation":
        from app.analytics.comparables_validation import run_battery
        from app.services.comparables import ComparableTradeService

        with SessionLocal() as db:
            service = ComparableTradeService(db)
            report = run_battery(service.rankable_corpus(), all_sides=service.corpus())
        print(json.dumps(report, indent=2, default=str))
        if report["failed"]:
            print(f"\nFAILED: {', '.join(report['failed'])}")
            sys.exit(2)
    elif command == "transaction-coverage":
        from app.ingestion.transactions.importer import coverage_summary

        with SessionLocal() as db:
            print(json.dumps(coverage_summary(db), indent=2, default=str))
    elif command == "pick-ownership":
        from app.ingestion.draft_picks import ownership_summary

        year = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year + 1
        round_number = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        with SessionLocal() as db:
            print(json.dumps(ownership_summary(db, year, round_number), indent=2, default=str))
    elif command == "purge-fixtures":
        from app.ingestion.fixtures import purge_fixtures

        apply = "--apply" in sys.argv[2:]
        with SessionLocal() as db:
            summary = purge_fixtures(db, dry_run=not apply)
        print(json.dumps(summary, indent=2, default=str))
        if not apply:
            print("\nDry run. Re-run with --apply to delete these rows.")
    elif command == "seed-demo":
        from app.ingestion.demo_seed import DemoSeedRefused, seed_demo

        try:
            with SessionLocal() as db:
                summary = seed_demo(db)
        except DemoSeedRefused as exc:
            print(f"seed-demo refused: {exc}")
            sys.exit(2)
        print(json.dumps(summary, indent=2, default=str))
    elif command == "adversarial-validation":
        from app.services.adversarial_validation import run_battery as adversarial_battery

        with SessionLocal() as db:
            report = adversarial_battery(db)
        print(json.dumps(report, indent=2, default=str))
        if report.get("failed"):
            print(f"\nFAILED: {', '.join(report['failed'])}")
            sys.exit(1)
    elif command == "sync-corpus-stats":
        from app.config import get_settings as _settings

        seasons = _settings().corpus_season_list
        with SessionLocal() as db:
            written = {
                "player_stats": jobs.sync_player_stats(db, seasons),
                "standings": sum(jobs.sync_standings(db, season) for season in seasons),
                "season_calendar": jobs.sync_season_calendar(db, seasons),
            }
        print(json.dumps({"seasons": seasons, "rows_written": written}, indent=2))
    elif command == "sync-all":
        with SessionLocal() as db:
            results = jobs.sync_all(db)
        print(json.dumps(results, indent=2, default=str))
    elif command in single_jobs:
        with SessionLocal() as db:
            rows = single_jobs[command](db)
        print(f"{command}: {rows} rows")
    elif command == "index-assets":
        from app.assets.indexer import index_assets

        with SessionLocal() as db:
            summary = index_assets(db)
        print(json.dumps(summary, indent=2, default=str))
    elif command == "import-stats-csv":
        from app.ingestion.stats_csv import import_stats_csv

        csv_path = sys.argv[2] if len(sys.argv) > 2 else "../data/imports/nba_player_stats_2026.csv"
        # The season is a property of the FILE, not of today's calendar: the default CSV holds
        # 2025-26 totals and must keep that label after the tools move to 2026-27.
        with SessionLocal() as db:
            summary = (
                import_stats_csv(db, csv_path, sys.argv[3])
                if len(sys.argv) > 3
                else import_stats_csv(db, csv_path)
            )
        print(json.dumps(summary, indent=2, default=str))
    elif command == "import-kaggle":
        from app.integrations.kaggle_nba.importer import import_history

        with SessionLocal() as db:
            summary = import_history(db)
        print(json.dumps(summary, indent=2, default=str))
    elif command == "validate-data":
        from app.ingestion.quality import validate_data

        with SessionLocal() as db:
            issues = validate_data(db)
        print(json.dumps(issues, indent=2, default=str))
    elif command == "build-features":
        from app.analytics.features import build_features

        with SessionLocal() as db:
            summary = build_features(db)
        print(json.dumps(summary, indent=2, default=str))
    elif command == "train":
        from app.analytics.train import train_all

        with SessionLocal() as db:
            summary = train_all(db, preserve_history="--preserve-history" in sys.argv[2:])
        print(json.dumps(summary, indent=2, default=str))
    elif command in ("backtest", "train-candidate", "candidates"):
        code = _modeling_command(command, sys.argv[2:])
        if code:
            sys.exit(code)
    elif command == "score":
        from app.analytics.score import score_all

        with SessionLocal() as db:
            summary = score_all(db)
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"unknown command: {command}\n{__doc__}")
        sys.exit(1)

    _ = get_settings()


if __name__ == "__main__":
    main()
