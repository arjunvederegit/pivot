"""Basketball-Reference contracts SNAPSHOT provider.

Parses a USER-DOWNLOADED snapshot of
https://www.basketball-reference.com/contracts/players.html saved locally
(default: data/imports/contracts/players.html). This module never performs live
scraping — the file must be downloaded by the user.

Data honesty rules:
- League-year column labels are derived from the table header, never hard-coded.
- Salary cells that are empty are skipped (season not under contract — not an error).
- Implausible (<= 0 or > $100M per season) or unparseable values are appended to
  ``rejected_rows`` with a reason, never corrected or guessed.
- ``guaranteed`` on each emitted record is BBRef's contract-level "remaining
  guaranteed" total (data-stat="remain_gtd"), repeated on every season record for
  that player; it is NOT a per-season figure.
- BBRef early-termination option cells (class salary-et) map cleanly to neither
  player_option nor team_option, so both flags are left None (unknown) for them.
- ``contract_type`` is **None**, not "standard". The page does not distinguish two-way
  from standard deals; asserting one is what made ROSTER_SIZE report `(pass, high)` on
  data nobody had, and what let a two-way salary inflate the salary-matching maximum.

What this provider CANNOT supply, measured against the saved page:

- ``contract_type`` — no column exists.
- ``signed_date`` — no column exists, so RECENTLY_SIGNED stays `unavailable`.
- ``no_trade_clause`` — no column exists.
- **an as-of date** — searched the whole 454 KB page for "last updated", "as of",
  "generated on", "data through", any ISO date and any "Month D, YYYY": none present.
  ``source_date`` is therefore the snapshot **file's** mtime, which truthfully records
  when the file was saved and is *not* the date the data was current. Every surface that
  renders it must say "snapshot saved", never "data as of". The R2b plan item "record
  source_date from the page's as-of date, not the file mtime" is not reachable through
  this provider; the `file` CSV provider carries a real ``source_date`` column.
"""

import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup, Comment, Tag

from app.canonical.identity import TEAM_CODE_ALIASES_BY_SOURCE
from app.core.logging import get_logger

from .base import ContractRecord

logger = get_logger(__name__)

TABLE_ID = "player-contracts"
SOURCE_NAME = "basketball-reference.com contracts snapshot"
MAX_PLAUSIBLE_SALARY = 100_000_000

# BBRef uses its own 3-letter codes for a few franchises; all others pass through.
BBREF_TO_NBA_TEAM = TEAM_CODE_ALIASES_BY_SOURCE["bbref"]

_SEASON_LABEL_RE = re.compile(r"^\d{4}-\d{2}$")
_SALARY_STAT_RE = re.compile(r"^y\d+$")


def _parse_dollars(text: str) -> int:
    """Parse "$12,345,678" -> 12345678. Raises ValueError on anything else."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    if not cleaned or not cleaned.lstrip("-").isdigit():
        raise ValueError(f"unparseable salary text {text.strip()!r}")
    return int(cleaned)


def _option_flags(cell: Tag) -> tuple[bool | None, bool | None]:
    """(player_option, team_option) from BBRef salary-cell css classes."""
    classes = {str(value) for value in cell.get_attribute_list("class") if value}
    if "salary-pl" in classes:
        return True, False
    if "salary-tm" in classes:
        return False, True
    if "salary-et" in classes:
        return None, None  # early termination option: neither flag fits; left unknown
    return False, False


class BasketballReferenceSnapshotProvider:
    name = "bbref_snapshot"
    parser_version = "1"

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.rejected_rows: list[dict] = []

    def fetch_contracts(self) -> list[ContractRecord]:
        path = Path(self.file_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Basketball-Reference contracts snapshot not found at {self.file_path}. "
                "Download https://www.basketball-reference.com/contracts/players.html in a "
                "browser and save it to data/imports/contracts/players.html (or set "
                "CONTRACT_DATA_FILE to its location). See docs on contract data imports "
                "for the workflow — this provider never scrapes live."
            )
        # When the snapshot file was saved — NOT the date the data was current. The page
        # carries no as-of marker of any kind (see the module docstring), so there is
        # nothing better available and nothing here pretends otherwise.
        source_date = date.fromtimestamp(path.stat().st_mtime)
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        table = self._find_table(soup)
        if table is None:
            raise ValueError(
                f"no table#{TABLE_ID} found in {self.file_path} "
                "(searched both the document and HTML comments)"
            )
        seasons = self._league_years(table)
        records: list[ContractRecord] = []
        for row in self._iter_rows(table):
            records.extend(self._parse_row(row, seasons, source_date))
        return records

    def _find_table(self, soup: BeautifulSoup) -> Tag | None:
        """BBRef embeds some tables inside HTML comments; handle both cases."""
        table = soup.find("table", id=TABLE_ID)
        if isinstance(table, Tag):
            return table
        for node in soup.find_all(string=True):
            if isinstance(node, Comment) and TABLE_ID in node:
                inner = BeautifulSoup(str(node), "html.parser")
                table = inner.find("table", id=TABLE_ID)
                if isinstance(table, Tag):
                    return table
        return None

    def _league_years(self, table: Tag) -> dict[str, str]:
        """Map salary column data-stat ("y1"..) -> league-year label from the header row."""
        seasons: dict[str, str] = {}
        for cell in table.select("thead th[data-stat]"):
            stat = str(cell.get("data-stat") or "")
            if not _SALARY_STAT_RE.match(stat):
                continue
            label = cell.get_text(strip=True)
            if not _SEASON_LABEL_RE.match(label):
                self.rejected_rows.append(
                    {
                        "player": None,
                        "season": None,
                        "reason": f"header column {stat!r} label {label!r} is not a "
                        "league year; column skipped",
                    }
                )
                continue
            seasons[stat] = label
        if not seasons:
            raise ValueError("could not derive league-year column labels from the table header")
        return seasons

    def _iter_rows(self, table: Tag) -> Iterator[Tag]:
        """Yield data rows, including rows wrapped in HTML comments inside the table."""
        body = table.find("tbody")
        container = body if isinstance(body, Tag) else table
        for tr in container.find_all("tr"):
            if isinstance(tr, Tag):
                yield tr
        for node in container.find_all(string=True):
            if isinstance(node, Comment) and "<tr" in node:
                inner = BeautifulSoup(str(node), "html.parser")
                for tr in inner.find_all("tr"):
                    if isinstance(tr, Tag):
                        yield tr

    def _parse_row(
        self, row: Tag, seasons: dict[str, str], source_date: date
    ) -> list[ContractRecord]:
        player_cell = row.find("td", attrs={"data-stat": "player"})
        if not isinstance(player_cell, Tag):
            return []  # repeated mid-table header / spacer rows carry no player td
        player_name = player_cell.get_text(strip=True)
        if not player_name:
            self._reject(None, None, "row has an empty player name")
            return []
        team_cell = row.find("td", attrs={"data-stat": "team_id"})
        team_raw = team_cell.get_text(strip=True).upper() if isinstance(team_cell, Tag) else ""
        if not team_raw:
            self._reject(player_name, None, "row has no team code")
            return []
        team = BBREF_TO_NBA_TEAM.get(team_raw, team_raw)
        guaranteed = self._parse_guaranteed(row, player_name)
        records: list[ContractRecord] = []
        for stat, season in seasons.items():
            cell = row.find("td", attrs={"data-stat": stat})
            if not isinstance(cell, Tag):
                continue
            text = cell.get_text(strip=True)
            if not text:
                continue  # season not under contract — not an error
            try:
                salary = _parse_dollars(text)
                if salary <= 0 or salary > MAX_PLAUSIBLE_SALARY:
                    raise ValueError(f"implausible salary {salary}")
            except ValueError as exc:
                self._reject(player_name, season, str(exc))
                continue
            player_option, team_option = _option_flags(cell)
            records.append(
                ContractRecord(
                    player_name=player_name,
                    team_abbreviation=team,
                    season=season,
                    salary=salary,
                    nba_player_id=None,  # identity matching happens downstream by name
                    # UNKNOWN, not standard. The BBRef contracts page does not
                    # distinguish two-way from standard deals, and asserting "standard"
                    # for all 886 rows flipped ROSTER_SIZE from (warning, medium) to
                    # (pass, high) league-wide on data nobody had — a 14-man roster of
                    # 11 standard + 3 two-way (illegal) would have reported `pass` at
                    # high confidence (C9). Two-way salaries are also excluded from
                    # salary matching, so the same assertion inflated `maximum_incoming`
                    # and approved trades the engine should refuse.
                    contract_type=None,
                    player_option=player_option,
                    team_option=team_option,
                    guaranteed=guaranteed,
                    source_name=SOURCE_NAME,
                    source_date=source_date,
                )
            )
        return records

    def _parse_guaranteed(self, row: Tag, player_name: str) -> int | None:
        """Contract-level remaining guaranteed total; None if absent, rejected if garbled."""
        cell = row.find("td", attrs={"data-stat": "remain_gtd"})
        if not isinstance(cell, Tag):
            return None
        text = cell.get_text(strip=True)
        if not text:
            return None
        try:
            value = _parse_dollars(text)
            if value < 0:
                raise ValueError(f"negative guaranteed total {value}")
            return value
        except ValueError as exc:
            self._reject(player_name, None, f"unparseable remain_gtd: {exc}")
            return None

    def _reject(self, player: str | None, season: str | None, reason: str) -> None:
        self.rejected_rows.append({"player": player, "season": season, "reason": reason})
        logger.warning(
            "rejected bbref contract data (player=%s season=%s): %s", player, season, reason
        )
