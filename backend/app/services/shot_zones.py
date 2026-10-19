"""R9.3A — a player's shot-zone distribution from BALLDONTLIE's ``shooting/by_zone`` season averages.

Descriptive only: where a player's attempts came from and how often they went in, set beside the league's
rate in the same zone. It is not a skill estimate and says nothing about why a player shot where he did
(role, teammates and scheme choose shots as much as ability does), and Pivot does not turn it into one.

Honest handling of what the stored figures are:

* The snapshot is stored with ``aggregation == "unspecified"`` (R8.3 did not reconcile it). This module
  *reconciles it itself, per request*: the six non-backcourt zones' attempts per game must add up to the same
  season's per-game field-goal attempts (``general/base``, verified against NBA.com in R8.3) within rounding.
  If they do not, the zones are **withheld** with the reason — never shown on an unverified unit.
* Figures are per game, rounded to a tenth by the provider, so a player with few games has coarse counts.
  Approximate season attempts (per-game x games) are returned so the UI can label a thin zone as thin.
* The league rate per zone is built from the same provider rows: sum of makes over sum of attempts across every
  player with a row, each weighted by games played. It is a reference line, not a prediction.
* The provider's own season and season type are used as stored; nothing is relabelled.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.provider_stats import provider_stat_rows
from app.canonical.seasons import SeasonType
from app.canonical.stats import numeric
from app.db.models import Player, ProviderStatSnapshot

PROVIDER = "balldontlie"
GROUP = "shooting/by_zone"

#: (key in the provider payload, label). The corner-3 aggregate duplicates left + right and is excluded.
ZONES: tuple[tuple[str, str], ...] = (
    ("restricted_area", "Restricted area"),
    ("in_the_paint_(non-ra)", "Paint (non-restricted)"),
    ("mid-range", "Mid-range"),
    ("left_corner_3", "Left corner 3"),
    ("right_corner_3", "Right corner 3"),
    ("above_the_break_3", "Above-the-break 3"),
)
#: Backcourt heaves are reported by the provider and belong to the reconciliation, not the court diagram.
RECONCILE_KEYS = (*(k for k, _ in ZONES), "backcourt")
RECONCILE_TOLERANCE = 0.4  # attempts per game: six zones each rounded to 0.1, plus the base figure's rounding
THIN_ZONE_ATTEMPTS = 25  # approximate season attempts below which a zone's percentage is flagged as thin


def _latest_scope(db: Session, player_id: str) -> tuple[str, str] | None:
    rows = db.execute(
        select(ProviderStatSnapshot.season, ProviderStatSnapshot.season_type)
        .where(
            ProviderStatSnapshot.provider == PROVIDER,
            ProviderStatSnapshot.entity_type == "player",
            ProviderStatSnapshot.internal_id == player_id,
            ProviderStatSnapshot.stat_group == GROUP,
            ProviderStatSnapshot.season_type == SeasonType.REGULAR.value,
        )
        .order_by(ProviderStatSnapshot.season.desc())
    ).all()
    return (rows[0][0], rows[0][1]) if rows else None


def league_zone_rates(db: Session, season: str) -> dict[str, dict[str, float]]:
    """``{zone: {fga, fgm, pct, players}}`` weighted by games, across every player with a row."""
    base = {
        row.internal_id: (row.games_played or numeric((row.values or {}).get("gp")) or 0)
        for row in db.scalars(
            select(ProviderStatSnapshot).where(
                ProviderStatSnapshot.provider == PROVIDER,
                ProviderStatSnapshot.entity_type == "player",
                ProviderStatSnapshot.season == season,
                ProviderStatSnapshot.season_type == SeasonType.REGULAR.value,
                ProviderStatSnapshot.stat_group == "general/base",
            )
        ).all()
    }
    totals: dict[str, list[float]] = {key: [0.0, 0.0, 0.0] for key, _ in ZONES}
    for row in db.scalars(
        select(ProviderStatSnapshot).where(
            ProviderStatSnapshot.provider == PROVIDER,
            ProviderStatSnapshot.entity_type == "player",
            ProviderStatSnapshot.season == season,
            ProviderStatSnapshot.season_type == SeasonType.REGULAR.value,
            ProviderStatSnapshot.stat_group == GROUP,
        )
    ).all():
        games = base.get(row.internal_id)
        if not games:
            continue
        values = row.values or {}
        for key, _ in ZONES:
            fga, fgm = numeric(values.get(f"{key}_fga")), numeric(values.get(f"{key}_fgm"))
            if fga is None or fgm is None:
                continue
            totals[key][0] += fga * games
            totals[key][1] += fgm * games
            totals[key][2] += 1.0
    return {
        key: {"fga": t[0], "fgm": t[1], "pct": (t[1] / t[0]) if t[0] > 0 else None, "players": t[2]}  # type: ignore[dict-item]
        for key, t in totals.items()
    }


def shot_zone_profile(db: Session, player_id: str) -> dict[str, Any]:
    player = db.get(Player, player_id)
    if player is None:
        return {"available": False, "reason": "no such player"}
    scope = _latest_scope(db, player_id)
    if scope is None:
        return {
            "available": False,
            "player_id": player_id,
            "reason": "No BALLDONTLIE shot-zone snapshot is stored for this player (no regular-season row).",
        }
    season, season_type = scope
    zone_rows = provider_stat_rows(db, [player_id], season=season, stat_group=GROUP)
    base_rows = provider_stat_rows(db, [player_id], season=season, stat_group="general/base")
    zone_row, base_row = zone_rows.rows.get(player_id), base_rows.rows.get(player_id)
    if zone_row is None:
        return {"available": False, "player_id": player_id, "reason": "shot-zone row missing"}
    values = zone_row.values or {}
    games = (base_row.games_played if base_row else None) or numeric((base_row.values or {}).get("gp") if base_row else None)
    base_fga = numeric((base_row.values or {}).get("fga")) if base_row else None
    total_pg = sum(numeric(values.get(f"{k}_fga")) or 0.0 for k in RECONCILE_KEYS)
    meta = zone_rows.meta.model_dump(mode="json")
    common = {
        "player_id": player_id,
        "provider": PROVIDER,
        "season": season,
        "season_type": season_type,
        "games_played": int(games) if games else None,
        "retrieved_at": zone_row.retrieved_at.isoformat() if zone_row.retrieved_at else None,
        "source_meta": meta,
    }
    if base_fga is None or not games:
        return {
            **common,
            "available": False,
            "reason": "The player's per-game field-goal attempts (general/base) are not stored, so the zone unit "
            "cannot be reconciled and the zones are withheld.",
        }
    # Both sides are rounded to a tenth by the provider; compare in tenths so 4.8 - 4.4 is 0.4, not 0.4000000000000004.
    if round(abs(total_pg - base_fga), 1) > RECONCILE_TOLERANCE:
        return {
            **common,
            "available": False,
            "reason": (
                f"Zone attempts sum to {total_pg:.1f} per game but the season's field-goal attempts are "
                f"{base_fga:.1f}; the unit does not reconcile, so the zones are withheld rather than shown."
            ),
        }
    league = league_zone_rates(db, season)
    zones = []
    for key, label in ZONES:
        fga, fgm = numeric(values.get(f"{key}_fga")), numeric(values.get(f"{key}_fgm"))
        if fga is None or fgm is None:
            continue
        pct = numeric(values.get(f"{key}_fg_pct"))
        if pct is None and fga > 0:
            pct = fgm / fga
        approx_total = fga * games
        lg = league.get(key, {})
        zones.append(
            {
                "key": key,
                "label": label,
                "attempts_per_game": fga,
                "makes_per_game": fgm,
                "share_of_attempts": (fga / total_pg) if total_pg > 0 else None,
                "fg_pct": pct if fga > 0 else None,
                "approx_season_attempts": round(approx_total),
                "thin_sample": approx_total < THIN_ZONE_ATTEMPTS,
                "league_fg_pct": lg.get("pct"),
                "league_players": lg.get("players"),
                "vs_league": (pct - lg["pct"]) if (pct is not None and fga > 0 and lg.get("pct") is not None) else None,
            }
        )
    return {
        **common,
        "available": True,
        "unit": "attempts and makes per game",
        "reconciliation": {
            "zone_attempts_per_game": round(total_pg, 2),
            "field_goal_attempts_per_game": base_fga,
            "tolerance": RECONCILE_TOLERANCE,
            "reconciled": True,
        },
        "zones": zones,
        "backcourt_attempts_per_game": numeric(values.get("backcourt_fga")) or 0.0,
        "notes": [
            "Descriptive: where the attempts came from and how often they went in. Role, teammates and scheme "
            "choose shots as much as ability does, so this is not a skill estimate.",
            "Figures are per game, rounded to a tenth by the provider; season attempts are approximate "
            f"(per game x games). A zone under {THIN_ZONE_ATTEMPTS} approximate attempts is flagged thin.",
            "League rate = makes / attempts across every player with a row, weighted by games played.",
        ],
        "limitations": [
            "BALLDONTLIE's shot-zone figures are stored as supplied; Pivot reconciled their unit against the "
            "season's per-game field-goal attempts for this player (above) but has not verified individual zone counts.",
        ],
    }
