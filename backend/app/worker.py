"""Background scheduler for containerized deployments (`docker compose up`).

Plain APScheduler over the same idempotent jobs the CLI runs — a deliberate,
documented simplification over Celery/RQ (see docs/decision-log.md): jobs are
CPU-light I/O batches on a small schedule, and every job is safe to re-run.
Schedules are configurable via environment variables."""

import os
from collections.abc import Callable

from apscheduler.schedulers.blocking import BlockingScheduler

from app.core.logging import configure_logging, get_logger
from app.db.base import SessionLocal

configure_logging()
logger = get_logger(__name__)


def job_map() -> dict[str, Callable[..., object]]:
    """The jobs the scheduler may run, by name.

    `validate_data` is here as well as the sync jobs. It re-derives every data-quality
    finding *and* prunes the ones resolved longer ago than the retention window, so
    running it on a schedule is what actually bounds `data_quality_issues` on a deployed
    instance — the growth this fixes was measured at 560 rows for 280 findings after two
    manual runs (R5-4).
    """
    from app.ingestion import jobs
    from app.ingestion.quality import validate_data

    return {
        "sync_rosters": jobs.sync_rosters,
        "prepare_league_season": jobs.prepare_league_season,
        "sync_standings": jobs.sync_standings,
        "sync_player_stats": jobs.sync_player_stats,
        "sync_team_stats": jobs.sync_team_stats,
        "sync_games": jobs.sync_games,
        "sync_contracts": jobs.sync_contracts,
        "validate_data": validate_data,
    }


def run_job(job_name: str) -> None:
    jobs_by_name = job_map()
    if job_name not in jobs_by_name:
        # A typo in a schedule used to raise `KeyError` *inside* the try, where the bare
        # `except Exception` logged it as a job failure. An unknown job is a configuration
        # error, and saying so is the difference between "the provider is down" and "this
        # job does not exist".
        logger.error(
            "worker job %s is not a known job (%s)",
            job_name,
            ", ".join(sorted(jobs_by_name)),
            extra={"job": job_name},
        )
        return
    with SessionLocal() as db:
        try:
            result = jobs_by_name[job_name](db)
            logger.info("worker job %s wrote %s rows", job_name, result, extra={"job": job_name})
        except Exception:
            logger.exception("worker job %s failed", job_name, extra={"job": job_name})


def build_scheduler() -> BlockingScheduler:
    """Constructed separately from `main` so the schedule can be asserted without
    starting a blocking loop."""
    scheduler = BlockingScheduler(timezone="UTC")
    roster_hours = int(os.environ.get("SYNC_ROSTERS_EVERY_HOURS", "6"))
    stats_hours = int(os.environ.get("SYNC_STATS_EVERY_HOURS", "24"))
    quality_hours = int(os.environ.get("VALIDATE_DATA_EVERY_HOURS", "24"))

    scheduler.add_job(run_job, "interval", hours=roster_hours, args=["sync_rosters"])
    scheduler.add_job(run_job, "interval", hours=roster_hours, args=["prepare_league_season"])
    scheduler.add_job(run_job, "interval", hours=roster_hours, args=["sync_standings"])
    scheduler.add_job(run_job, "interval", hours=stats_hours, args=["sync_player_stats"])
    scheduler.add_job(run_job, "interval", hours=stats_hours, args=["sync_team_stats"])
    scheduler.add_job(run_job, "interval", hours=stats_hours, args=["sync_games"])
    scheduler.add_job(run_job, "interval", hours=stats_hours, args=["sync_contracts"])
    scheduler.add_job(run_job, "interval", hours=quality_hours, args=["validate_data"])
    return scheduler


def main() -> None:
    scheduler = build_scheduler()
    logger.info(
        "worker started (%d jobs; rosters/standings every %sh, stats/games and the "
        "data-quality sweep daily)",
        len(scheduler.get_jobs()),
        os.environ.get("SYNC_ROSTERS_EVERY_HOURS", "6"),
    )
    scheduler.start()


if __name__ == "__main__":
    main()
