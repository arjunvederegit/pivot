"""Compatibility re-export. The implementation moved to ``app.canonical.identity`` in R8.

Contract and transaction importers (and their tests) import these names from here; the
definitions, the measurements behind the tiers, and the new provider-id tier live in the
canonical module so every consumer resolves identity the same way.
"""

from app.canonical.identity import (
    SUFFIXES,
    PlayerHints,
    PlayerIdentityIndex,
    Resolution,
    _normalize,
    strip_suffix,
    unaccent,
)

__all__ = [
    "SUFFIXES",
    "PlayerHints",
    "PlayerIdentityIndex",
    "Resolution",
    "_normalize",
    "strip_suffix",
    "unaccent",
]
