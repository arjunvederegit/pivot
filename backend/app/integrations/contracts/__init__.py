"""Contract data providers.

nba_api does not supply contract or salary detail, so contracts are an optional,
clearly-labeled secondary dataset. When no provider is configured the rest of the
application still works and salary-dependent features report `unavailable` — they are
never estimated or invented."""

from pathlib import Path

from app.config import get_settings

# Re-exported for the existing `from app.integrations.contracts import ContractRecord`
# call sites. The definitions live in `base` so the provider modules can import them
# without importing this package back — see `base.py` for the concurrency failure that
# cycle caused once a provider was actually configured.
from .base import ContractProvider, ContractRecord

__all__ = [
    "ContractProvider",
    "ContractRecord",
    "DEFAULT_BBREF_SNAPSHOT",
    "contract_source_path",
    "get_contract_provider",
]

DEFAULT_BBREF_SNAPSHOT = "data/imports/contracts/players.html"


def contract_source_path() -> Path | None:
    """Where the configured provider expects its file, regardless of whether it exists."""
    from app.config import BACKEND_DIR

    settings = get_settings()
    path = settings.contract_data_path
    if settings.contract_data_provider == "file":
        return path or (Path(settings.contract_data_file) if settings.contract_data_file else None)
    if settings.contract_data_provider == "bbref_snapshot":
        return path or (BACKEND_DIR.parent / DEFAULT_BBREF_SNAPSHOT)
    return None


class StoredBalldontlieContracts:
    """Marker for ``CONTRACT_DATA_PROVIDER=balldontlie``: contract rows are written by
    ``sync_contracts_from_balldontlie`` (it needs a database session and an API client), so this
    provider has nothing to fetch. It exists so every reader that asks "is a contract provider
    configured?" gets the truthful answer once the owner has opted in (R8.3)."""

    name = "balldontlie"

    def fetch_contracts(self) -> list[ContractRecord]:
        return []


def get_contract_provider() -> "ContractProvider | None":
    """Returns the configured provider, or None (the honest default).

    "Configured" means the env var is set **and** the file it points at is readable. An
    env var alone made `contract_provider_configured` True product-wide, which changes
    what several rules say: `RECENTLY_SIGNED` switches from silent to `unavailable`, and
    the empty-state copy switches from "no provider is configured" to "the configured
    provider lacks these players". A typo'd path therefore blamed the data instead of
    the configuration, and the message told the operator to look in the wrong place.

    Paths resolve against the repo root (`Settings.contract_data_path`), not the process
    working directory: `make` runs backend commands from `backend/`, where a relative
    `data/imports/…` silently found nothing.
    """
    settings = get_settings()
    if settings.contract_data_provider == "balldontlie":
        return StoredBalldontlieContracts()
    source = contract_source_path()
    if source is None or not source.is_file():
        return None
    if settings.contract_data_provider == "file":
        from .file_provider import FileContractProvider

        return FileContractProvider(str(source))
    if settings.contract_data_provider == "bbref_snapshot":
        from .bbref_provider import BasketballReferenceSnapshotProvider

        return BasketballReferenceSnapshotProvider(str(source))
    return None
