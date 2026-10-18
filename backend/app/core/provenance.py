"""One place that turns a stored `source_provider` key into the upstream a reader sees.

This lives in `core` rather than beside the API schemas because both the serving layer
and the services that build source cards and decision memos need it, and a service must
not import from `app.api`.

The rule the whole module exists to enforce: **never name a source that was not read off
the row.** Before this, `Provenance` defaulted to `nba_api` / `NBA.com` and no caller ever
overrode it, so a synthetic demo row — correctly stamped `demo_seed` in storage, correctly
refused entry to a real database, correctly guarded by a Playwright roster-name check —
was still *described to the client as NBA.com data*. Four guards stopped synthetic data
leaking; none stopped it being mislabelled.
"""

UPSTREAM_BY_PROVIDER: dict[str, str] = {
    "nba_api": "NBA.com via nba_api",
    "nba_api_static": "NBA.com via nba_api (static data)",
    "demo_seed": "Synthetic demo data (not real NBA data)",
    "user_import_csv": "User-imported CSV",
    "bbref_snapshot": "Basketball-Reference (user-imported snapshot)",
    "bbref_transactions": "Basketball-Reference (user-imported snapshot)",
    "realgm_future_drafts": "RealGM (user-imported snapshot)",
    "local_assets": "Local asset index",
    "file": "User-imported file",
    # Ask Pivot's research sandbox (app/assistant_rag). Fictional league, fictional players;
    # it shares no table, id or name with the NBA data above.
    "assistant_synthetic_corpus": "Fictional Ask Pivot research corpus (synthetic, not NBA data)",
}

UNKNOWN_UPSTREAM = "unknown source"


def upstream_for(provider: str | None) -> str:
    """The human-readable upstream for a stored provider key.

    Never guesses. A missing provider is `unknown source`; an unmapped one is returned
    verbatim, so a new ingester shows up as its own key instead of borrowing NBA.com's
    name until someone remembers to add it here.
    """
    if not provider:
        return UNKNOWN_UPSTREAM
    return UPSTREAM_BY_PROVIDER.get(provider, provider)


def describe_providers(providers: "set[str] | list[str]") -> str:
    """Name every upstream a collection of rows actually came from.

    Used wherever an endpoint or a report serves many rows under one `source` label. A
    table that holds more than one provider — `player_season_stats` carries nba_api rows
    and user-imported CSV rows — is described as both rather than as whichever one the
    caller was originally written for.
    """
    names = sorted({upstream_for(p) for p in providers if p})
    if not names:
        return UNKNOWN_UPSTREAM
    return " + ".join(names)
