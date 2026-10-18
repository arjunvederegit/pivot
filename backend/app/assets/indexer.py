"""Local media-asset indexer: maps user-supplied logo/photo files to team/player
identities in the database.

Files live outside git (see Settings.logos_dir / player_images_dir) and are keyed by
abbreviation (logos) or display name (player photo folders). Name resolution is
conservative: exact match, then diacritic/punctuation-normalized match; anything
ambiguous or unknown is stored as an unmatched MediaAsset row for review — never
guessed into an identity.
"""

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assets.normalize import DERIVED_DIRNAME, normalize_logo
from app.canonical.identity import TEAM_CODE_ALIASES_BY_SOURCE
from app.canonical.rosters import current_roster_clause
from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import DataQualityIssue, MediaAsset, Player, RosterEntry, Team
from app.ingestion.quality import upsert_issue
from app.ingestion.runs import sync_run

logger = get_logger(__name__)

SOURCE_PROVIDER = "local_assets"

# Logo filename stems that do not equal the NBA abbreviation lowercased.
LOGO_STEM_OVERRIDES = {k.lower(): v for k, v in TEAM_CODE_ALIASES_BY_SOURCE["asset_filename"].items()}

_CONTENT_TYPES = {
    ".png": "image/png",
    ".gif": "image/gif",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}
_PUNCTUATION_RE = re.compile(r"[.,'’-]")
_PRIMARY_IMAGE_RE = re.compile(r"^image_(\d+)\.(jpe?g|png|gif|webp)$", re.IGNORECASE)

_UNMATCHED_LOGO_CHECK = "asset_unmatched_team_logo"
_UNMATCHED_PLAYER_CHECK = "asset_unmatched_player_dir"


def _image_size(path: Path) -> tuple[int | None, int | None]:
    """Pixel dimensions from the file header, or (None, None). Never raises."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.width, image.height
    except Exception:  # noqa: BLE001 - unreadable/unsupported files are indexed without dimensions
        return None, None


def normalize_name(name: str) -> str:
    """Lowercase, strip diacritics, remove punctuation, collapse spaces, drop
    generational suffix tokens (jr/sr/ii/iii/iv/v)."""
    decomposed = unicodedata.normalize("NFD", name.lower())
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    no_punct = _PUNCTUATION_RE.sub("", no_marks)
    tokens = [t for t in no_punct.split() if t not in _SUFFIX_TOKENS]
    return " ".join(tokens)


def _record_issue(db: Session, check_name: str, message: str, entity: str | None = None) -> None:
    # Upserted on (check, entity) rather than inserted. Every run re-derives these
    # findings, so an insert left one row per finding per run: measured at 560 rows for
    # 280 findings after two `index-assets` runs (R5-4).
    upsert_issue(db, check_name, message, severity="warning", entity=entity)


def _upsert(db: Session, existing: dict[tuple[str, str], MediaAsset], values: dict) -> MediaAsset:
    key = (values["entity_type"], values["file_path"])
    asset = existing.get(key)
    if asset is None:
        asset = MediaAsset(**values)
        db.add(asset)
        existing[key] = asset
    else:
        for field, value in values.items():
            setattr(asset, field, value)
    return asset


def _primary_image(dir_path: Path) -> Path | None:
    """Lowest-N ``Image_<N>.<ext>`` in the folder; falls back to the alphabetically
    first image file if none match the pattern. None when the folder has no images."""
    best: tuple[int, Path] | None = None
    fallback: Path | None = None
    for entry in sorted(dir_path.iterdir()):
        if not entry.is_file() or entry.suffix.lower() not in _CONTENT_TYPES:
            continue
        if fallback is None:
            fallback = entry
        match = _PRIMARY_IMAGE_RE.match(entry.name)
        if match:
            n = int(match.group(1))
            if best is None or n < best[0]:
                best = (n, entry)
    return best[1] if best else fallback


def _resolve_player(
    dirname: str,
    by_exact: dict[str, list[Player]],
    by_normalized: dict[str, list[Player]],
) -> tuple[Player | None, str, str]:
    """Resolve a photo folder name to a player. Returns (player, match_method,
    confidence); (None, ..., ...) when unknown or ambiguous — never a guess."""
    candidates = by_exact.get(dirname.lower())
    method, confidence = "exact_name", "high"
    if not candidates:
        candidates = by_normalized.get(normalize_name(dirname))
        method, confidence = "normalized_name", "medium"
    if not candidates:
        return None, "normalized_name", "unmatched"
    if len(candidates) > 1:
        active = [p for p in candidates if p.is_active]
        if len(active) != 1:
            return None, method, "unmatched"  # ambiguous even after the active filter
        return active[0], method, confidence
    return candidates[0], method, confidence


def index_assets(db: Session) -> dict:
    """Scan logos_dir and player_images_dir, upserting one MediaAsset per logo file
    and one per player folder (its primary headshot). Idempotent on
    (entity_type, file_path)."""
    settings = get_settings()
    logos_dir = settings.logos_dir
    players_dir = settings.player_images_dir
    summary = {
        "teams_indexed": 0,
        "teams_unmatched": 0,
        "players_indexed": 0,
        "players_unmatched": 0,
        "player_dirs_scanned": 0,
    }
    with sync_run(db, "index_assets") as run:
        now = datetime.now(UTC)
        existing = {(a.entity_type, a.file_path): a for a in db.scalars(select(MediaAsset)).all()}
        # Unmatched-asset findings are re-derived every run; resolve the old ones.
        open_issues = db.scalars(
            select(DataQualityIssue).where(
                DataQualityIssue.check_name.in_((_UNMATCHED_LOGO_CHECK, _UNMATCHED_PLAYER_CHECK)),
                DataQualityIssue.resolved_at.is_(None),
            )
        ).all()
        for issue in open_issues:
            issue.resolved_at = now

        provenance = {
            "source_provider": SOURCE_PROVIDER,
            "source_retrieved_at": now,
            "ingestion_run_id": run.id,
        }

        teams_by_abbr = {t.abbreviation.upper(): t for t in db.scalars(select(Team)).all()}
        if logos_dir.is_dir():
            for path in sorted(logos_dir.iterdir()):
                if not path.is_file() or path.suffix.lower() not in _CONTENT_TYPES:
                    continue  # e.g. dataset-metadata.json
                if path.parent.name == DERIVED_DIRNAME:
                    continue
                stem = path.stem.lower()
                abbr = LOGO_STEM_OVERRIDES.get(stem, stem).upper()
                team = teams_by_abbr.get(abbr)
                # Some supplied logos sit on an opaque white card; serve a
                # transparent derivative so crests render consistently on dark.
                derived = normalize_logo(path, logos_dir / DERIVED_DIRNAME)
                file_path = f"{DERIVED_DIRNAME}/{derived.name}" if derived else path.name
                content_type = "image/png" if derived else _CONTENT_TYPES[path.suffix.lower()]
                values = {
                    **provenance,
                    "entity_type": "team",
                    "file_path": file_path,
                    "source_record_id": path.name,
                    "content_type": content_type,
                    "is_primary": True,
                    "match_method": "abbreviation",
                    "source_label": stem,
                }
                values["width"], values["height"] = _image_size(derived or path)
                if team is None:
                    values.update(
                        team_id=None,
                        nba_id=None,
                        confidence="unmatched",
                        alt_text=f"Unmatched logo file {path.name}",
                    )
                    summary["teams_unmatched"] += 1
                    _record_issue(
                        db,
                        _UNMATCHED_LOGO_CHECK,
                        f"logo file {path.name} maps to no team abbreviation",
                        entity=path.name,
                    )
                else:
                    values.update(
                        team_id=team.id,
                        nba_id=team.nba_team_id,
                        confidence="high",
                        alt_text=f"{team.full_name} logo",
                    )
                    summary["teams_indexed"] += 1
                _upsert(db, existing, values)
                run.rows_written += 1
        else:
            logger.warning("logos dir missing: %s", logos_dir)

        players = db.scalars(select(Player)).all()
        by_exact: dict[str, list[Player]] = {}
        by_normalized: dict[str, list[Player]] = {}
        for player in players:
            by_exact.setdefault(player.full_name.lower(), []).append(player)
            by_normalized.setdefault(normalize_name(player.full_name), []).append(player)

        if players_dir.is_dir():
            for dir_path in sorted(p for p in players_dir.iterdir() if p.is_dir()):
                summary["player_dirs_scanned"] += 1
                dirname = dir_path.name
                primary = _primary_image(dir_path)
                file_path = f"{dirname}/{primary.name}" if primary else dirname
                content_type = (
                    _CONTENT_TYPES[primary.suffix.lower()]
                    if primary
                    else "application/octet-stream"
                )
                matched, method, confidence = _resolve_player(dirname, by_exact, by_normalized)
                values = {
                    **provenance,
                    "entity_type": "player",
                    "file_path": file_path,
                    "source_record_id": file_path,
                    "content_type": content_type,
                    "is_primary": True,
                    "match_method": method,
                    "source_label": dirname,
                }
                if primary is not None:
                    values["width"], values["height"] = _image_size(primary)
                if matched is None or primary is None:
                    values.update(
                        player_id=None,
                        nba_id=None,
                        confidence="unmatched",
                        alt_text=f"Unmatched player photo folder {dirname}",
                    )
                    summary["players_unmatched"] += 1
                    reason = "no image files" if primary is None else "no unambiguous player match"
                    _record_issue(
                        db,
                        _UNMATCHED_PLAYER_CHECK,
                        f"photo folder {dirname!r}: {reason}",
                        entity=dirname,
                    )
                else:
                    values.update(
                        player_id=matched.id,
                        nba_id=matched.nba_player_id,
                        confidence=confidence,
                        alt_text=f"Photo of {matched.full_name}",
                    )
                    summary["players_indexed"] += 1
                _upsert(db, existing, values)
                run.rows_written += 1
        else:
            logger.warning("player images dir missing: %s", players_dir)

        run.detail = dict(summary)
        db.commit()
    return summary


def coverage(db: Session) -> dict:
    """Coverage of matched assets: logos over all teams (30 in production), photos
    over currently rostered players (RosterEntry.is_current)."""
    teams_total = db.scalar(select(func.count(Team.id))) or 0
    teams_with_logo = (
        db.scalar(
            select(func.count(func.distinct(MediaAsset.team_id))).where(
                MediaAsset.entity_type == "team", MediaAsset.team_id.is_not(None)
            )
        )
        or 0
    )
    teams_unmatched_files = (
        db.scalar(
            select(func.count(MediaAsset.id)).where(
                MediaAsset.entity_type == "team", MediaAsset.confidence == "unmatched"
            )
        )
        or 0
    )
    rostered_players = (
        db.scalar(
            select(func.count(func.distinct(RosterEntry.player_id))).where(
                current_roster_clause()
            )
        )
        or 0
    )
    rostered_with_photo = (
        db.scalar(
            select(func.count(func.distinct(RosterEntry.player_id)))
            .select_from(RosterEntry)
            .join(MediaAsset, MediaAsset.player_id == RosterEntry.player_id)
            .where(
                current_roster_clause(),
                MediaAsset.entity_type == "player",
                MediaAsset.is_primary,
            )
        )
        or 0
    )
    players_with_photo = (
        db.scalar(
            select(func.count(func.distinct(MediaAsset.player_id))).where(
                MediaAsset.entity_type == "player", MediaAsset.player_id.is_not(None)
            )
        )
        or 0
    )
    unmatched_player_dirs = (
        db.scalar(
            select(func.count(MediaAsset.id)).where(
                MediaAsset.entity_type == "player", MediaAsset.confidence == "unmatched"
            )
        )
        or 0
    )
    usage: dict[str, int] = {
        str(status): int(count)
        for status, count in db.execute(
            select(MediaAsset.usage_status, func.count(MediaAsset.id)).group_by(
                MediaAsset.usage_status
            )
        ).all()
    }
    licensed = usage.get("licensed", 0)
    return {
        # Rights, not coverage: a file being servable says nothing about the right to show
        # it. `unverified` is the default and is never inferred from a file being reachable.
        "usage_status": usage,
        "assets_cleared_for_public_display": licensed,
        "rights_note": (
            None
            if licensed
            else "No indexed asset has a recorded licence. These files are for local "
            "development only; see docs/r8/resource-research.md before any public display."
        ),
        "team_logo_coverage": round(teams_with_logo / teams_total, 4) if teams_total else 0.0,
        "teams_total": teams_total,
        "teams_with_logo": teams_with_logo,
        "teams_unmatched_files": teams_unmatched_files,
        "player_photo_coverage": round(rostered_with_photo / rostered_players, 4)
        if rostered_players
        else 0.0,
        "rostered_players": rostered_players,
        "rostered_players_with_photo": rostered_with_photo,
        "players_with_photo": players_with_photo,
        "unmatched_player_dirs": unmatched_player_dirs,
    }
