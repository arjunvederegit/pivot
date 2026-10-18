"""Import parsed Basketball-Reference trades into the local database.

## What this refuses to do

**No fuzzy matching.** A printed name becomes a player through the same tiered index the
contract import uses — exact, unaccented, suffix-insensitive — and a name that lands on
two players is recorded as `ambiguous`, not resolved by picking. The tie-break population
is the players who recorded a season in the trade's **own** season, not the current roster:
a 2016-17 trade is not about whoever holds that name today.

**No back-filled franchises.** Basketball-Reference prints `BRK`, `CHO` and `PHO` where
this database holds `BKN`, `CHA` and `PHX`. Those three are an explicit alias table, not a
prefix match; an abbreviation outside it is reported unresolved rather than guessed at.

**No invented certainty about picks.** A pick's conveyance class comes from the source's
own note, and where a trade moved two picks of the same year and round the note is attached
to both with `note_binding_ambiguous` set.

## Idempotence

`source_record_id` is `season:date:sha1(source_text)`, so re-importing the same snapshot
produces the same rows. The import replaces every row from this provider rather than
merging, because a corrected upstream page should not leave the old sentence behind.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.identity import TEAM_CODE_ALIASES_BY_SOURCE
from app.core.logging import get_logger
from app.db.models import (
    DataQualityIssue,
    HistoricalTrade,
    HistoricalTradeAsset,
    PlayerSeasonStats,
    Team,
)
from app.ingestion.identity import PlayerIdentityIndex
from app.ingestion.quality import upsert_issue
from app.ingestion.runs import sync_run
from app.ingestion.transactions.fetch import (
    PROVENANCE_FILE,
    SOURCE_PROVIDER,
    season_label,
    transactions_dir,
)
from app.ingestion.transactions.parse import ParsedTrade, parse_season_page

logger = get_logger(__name__)

#: Basketball-Reference's franchise abbreviations that differ from NBA.com's. Explicit,
#: because these are exactly the three where a prefix or fuzzy match would silently place
#: a trade on the wrong franchise.
ABBREVIATION_ALIASES = TEAM_CODE_ALIASES_BY_SOURCE["bbref"]

UNRESOLVED_PLAYER_CHECK = "historical_trade_unresolved_player"
UNPARSED_ASSET_CHECK = "historical_trade_unparsed_asset"
UNRESOLVED_TEAM_CHECK = "historical_trade_unresolved_team"


def _canonical_abbr(abbr: str) -> str:
    return ABBREVIATION_ALIASES.get(abbr.upper(), abbr.upper())


def _record_id(trade: ParsedTrade) -> str:
    digest = hashlib.sha1(trade.source_text.encode("utf-8")).hexdigest()[:12]
    return f"{trade.season}:{trade.transaction_date.isoformat()}:{digest}"


def _resolve_exception_city(city: str, participants: list[Team]) -> Team | None:
    """A trade-exception note names a city. Resolve it against **this trade's** teams only.

    Two participants sharing a city — the two Los Angeles franchises — resolves to neither.
    The city string can also carry a leading abbreviation the note ran together with it
    (`DET Indiana`), so the suffix is tried as well; the leading token is never the answer
    on its own.
    """
    tokens = city.split()
    for start in range(len(tokens)):
        candidate = " ".join(tokens[start:]).strip().lower()
        if not candidate:
            continue
        matches = [t for t in participants if (t.city or "").lower() == candidate]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


def _load_provenance(directory: Path) -> dict[str, dict]:
    path = directory / PROVENANCE_FILE
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded.get("pages", {}) if isinstance(loaded, dict) else {}


def _season_player_ids(db: Session, season: str) -> set[str]:
    return set(
        db.scalars(
            select(PlayerSeasonStats.player_id).where(PlayerSeasonStats.season == season)
        ).all()
    )


def import_transactions(db: Session, directory: str | None = None) -> dict:
    """Replace this provider's historical trades with the local snapshots' contents."""
    target = transactions_dir(directory)
    snapshots = sorted(target.glob("NBA_*_transactions.html"))
    if not snapshots:
        return {
            "error": f"no transaction snapshots in {target}",
            "hint": "run `make fetch-transactions FROM=2017 TO=2026` first",
            "imported": 0,
        }

    provenance = _load_provenance(target)
    summary: dict[str, Any] = {
        "directory": str(target),
        "seasons": [],
        "trades_parsed": 0,
        "trades_imported": 0,
        "multi_team_trades": 0,
        "asset_legs": 0,
        "player_legs": 0,
        "player_legs_resolved": 0,
        "player_legs_unresolved": 0,
        "pick_legs": 0,
        "cash_legs": 0,
        "unparsed_assets": 0,
        "unresolved_team_abbreviations": [],
        "resolution_methods": {},
        "conveyance": {},
        "note_binding_ambiguous": 0,
        "empty_legs": 0,
        "n_teams_disagreements": 0,
    }

    with sync_run(db, "import_transactions") as run:
        now = datetime.now(UTC)
        for existing in db.scalars(
            select(HistoricalTrade).where(HistoricalTrade.source_provider == SOURCE_PROVIDER)
        ).all():
            db.delete(existing)
        for issue in db.scalars(
            select(DataQualityIssue).where(
                DataQualityIssue.check_name.in_(
                    (UNRESOLVED_PLAYER_CHECK, UNPARSED_ASSET_CHECK, UNRESOLVED_TEAM_CHECK)
                ),
                DataQualityIssue.resolved_at.is_(None),
            )
        ).all():
            issue.resolved_at = now
        db.flush()

        teams_by_abbr = {t.abbreviation.upper(): t for t in db.scalars(select(Team)).all()}
        index_cache: dict[str, PlayerIdentityIndex] = {}
        rows_written = 0

        for snapshot in snapshots:
            end_year = int(snapshot.stem.split("_")[1])
            season = season_label(end_year)
            trades, report = parse_season_page(
                snapshot.read_text(encoding="utf-8", errors="replace"), season
            )
            page = provenance.get(season, {})
            retrieved_at = (
                datetime.fromisoformat(page["retrieved_at"])
                if page.get("retrieved_at")
                else datetime.fromtimestamp(snapshot.stat().st_mtime, tz=UTC)
            )
            if season not in index_cache:
                index_cache[season] = PlayerIdentityIndex(
                    db, season, preferred_player_ids=_season_player_ids(db, season)
                )
            index = index_cache[season]

            season_summary = {
                "season": season,
                "source_file": snapshot.name,
                "source_url": page.get("url"),
                "source_sha256": page.get("sha256"),
                "source_retrieved_at": retrieved_at.isoformat(),
                "trade_paragraphs": report.trade_paragraphs,
                "trades_parsed": report.trades_parsed,
                "trades_unparsed": len(report.trades_unparsed),
                "multi_team": report.multi_team,
                "unparsed_assets": len(report.unparsed_assets),
            }
            summary["trades_parsed"] += report.trades_parsed
            summary["multi_team_trades"] += report.multi_team

            for trade in trades:
                participants = []
                unresolved_abbrs = []
                for abbr in trade.team_abbrs:
                    team = teams_by_abbr.get(_canonical_abbr(abbr))
                    if team is None:
                        unresolved_abbrs.append(abbr)
                    else:
                        participants.append(team)
                if unresolved_abbrs:
                    summary["unresolved_team_abbreviations"].extend(unresolved_abbrs)
                    upsert_issue(
                        db,
                        UNRESOLVED_TEAM_CHECK,
                        f"{season} {trade.transaction_date}: unresolved franchise "
                        f"abbreviation(s) {', '.join(sorted(set(unresolved_abbrs)))}",
                        severity="error",
                        entity=_record_id(trade),
                    )
                if trade.n_teams != len(trade.team_abbrs):
                    summary["n_teams_disagreements"] += 1

                exception_team_ids: list[str] = []
                exception_unresolved: list[str] = []
                for city in trade.trade_exception_cities:
                    resolved = _resolve_exception_city(city, participants)
                    if resolved is None:
                        exception_unresolved.append(city)
                    elif resolved.id not in exception_team_ids:
                        exception_team_ids.append(resolved.id)

                row = HistoricalTrade(
                    season=season,
                    transaction_date=trade.transaction_date,
                    n_teams=trade.n_teams,
                    source_text=trade.source_text,
                    notes_text=trade.notes_text or None,
                    unparsed_assets=list(trade.unparsed_assets),
                    trade_exception_team_ids=exception_team_ids,
                    trade_exception_unresolved=exception_unresolved,
                    source_provider=SOURCE_PROVIDER,
                    source_record_id=_record_id(trade),
                    source_retrieved_at=retrieved_at,
                    ingestion_run_id=run.id,
                )
                db.add(row)
                db.flush()
                rows_written += 1
                summary["trades_imported"] += 1

                if trade.unparsed_assets:
                    summary["unparsed_assets"] += len(trade.unparsed_assets)
                    upsert_issue(
                        db,
                        UNPARSED_ASSET_CHECK,
                        f"{season} {trade.transaction_date}: asset phrase(s) not recognized "
                        f"— {'; '.join(trade.unparsed_assets)}",
                        severity="warning",
                        entity=row.source_record_id,
                    )

                for leg in trade.legs:
                    from_team = teams_by_abbr.get(_canonical_abbr(leg.from_abbr))
                    to_team = teams_by_abbr.get(_canonical_abbr(leg.to_abbr))
                    common = {
                        "trade_id": row.id,
                        "from_team_id": from_team.id if from_team else None,
                        "to_team_id": to_team.id if to_team else None,
                        "from_abbreviation": _canonical_abbr(leg.from_abbr),
                        "to_abbreviation": _canonical_abbr(leg.to_abbr),
                    }
                    if not (leg.players or leg.picks or leg.cash or leg.unparsed_assets):
                        summary["empty_legs"] += 1
                    for parsed_player in leg.players:
                        resolution = index.resolve(nba_player_id=None, name=parsed_player.name)
                        method = resolution.method
                        summary["player_legs"] += 1
                        summary["resolution_methods"][method] = (
                            summary["resolution_methods"].get(method, 0) + 1
                        )
                        if resolution.resolved:
                            summary["player_legs_resolved"] += 1
                        else:
                            summary["player_legs_unresolved"] += 1
                            upsert_issue(
                                db,
                                UNRESOLVED_PLAYER_CHECK,
                                f"{season} {trade.transaction_date}: "
                                f"'{parsed_player.name}' did not resolve to exactly one "
                                f"player ({method})",
                                severity="warning",
                                entity=f"{row.source_record_id}:{parsed_player.slug}",
                            )
                        db.add(
                            HistoricalTradeAsset(
                                asset_type="player",
                                player_id=(
                                    resolution.player.id if resolution.player else None
                                ),
                                player_name=parsed_player.name,
                                source_player_slug=parsed_player.slug,
                                resolution_method=method,
                                via_draft_rights=parsed_player.via_draft_rights,
                                **common,
                            )
                        )
                        rows_written += 1
                    for pick in leg.picks:
                        summary["pick_legs"] += 1
                        summary["conveyance"][pick.conveyance] = (
                            summary["conveyance"].get(pick.conveyance, 0) + 1
                        )
                        if pick.note_binding_ambiguous:
                            summary["note_binding_ambiguous"] += 1
                        db.add(
                            HistoricalTradeAsset(
                                asset_type="pick",
                                draft_year=pick.draft_year,
                                round_number=pick.round_number,
                                conveyance=pick.conveyance,
                                note_text=pick.note_text,
                                note_binding_ambiguous=pick.note_binding_ambiguous,
                                later_selected=pick.later_selected,
                                **common,
                            )
                        )
                        rows_written += 1
                    if leg.cash:
                        summary["cash_legs"] += 1
                        db.add(HistoricalTradeAsset(asset_type="cash", **common))
                        rows_written += 1

            summary["seasons"].append(season_summary)

        summary["asset_legs"] = (
            summary["player_legs"] + summary["pick_legs"] + summary["cash_legs"]
        )
        summary["unresolved_team_abbreviations"] = sorted(
            set(summary["unresolved_team_abbreviations"])
        )
        run.rows_written = rows_written
        run.detail = {
            "trades_imported": summary["trades_imported"],
            "player_legs_resolved": summary["player_legs_resolved"],
            "player_legs_unresolved": summary["player_legs_unresolved"],
        }
        db.commit()

    resolved = summary["player_legs_resolved"]
    total = summary["player_legs"]
    summary["player_resolution_rate"] = round(resolved / total, 4) if total else None
    return summary


def coverage_summary(db: Session) -> dict:
    """What the imported corpus contains, without importing anything."""
    trades = db.scalars(
        select(HistoricalTrade).where(HistoricalTrade.source_provider == SOURCE_PROVIDER)
    ).all()
    by_season: dict[str, int] = {}
    for trade in trades:
        by_season[trade.season] = by_season.get(trade.season, 0) + 1
    assets = db.scalars(select(HistoricalTradeAsset)).all()
    player_legs = [a for a in assets if a.asset_type == "player"]
    return {
        "trades": len(trades),
        "seasons": dict(sorted(by_season.items())),
        "multi_team_trades": sum(1 for t in trades if t.n_teams > 2),
        "asset_legs": len(assets),
        "player_legs": len(player_legs),
        "player_legs_resolved": sum(1 for a in player_legs if a.player_id is not None),
        "pick_legs": sum(1 for a in assets if a.asset_type == "pick"),
        "cash_legs": sum(1 for a in assets if a.asset_type == "cash"),
        "trades_with_unparsed_assets": sum(1 for t in trades if t.unparsed_assets),
    }
