"""R9.2 — the reusable Player Intelligence Engine.

``components``    integer makes/attempts recovered from what the provider stored
``panel``         R9.1's player-season panel plus those counts
``rate_models``   sampling noise, ability and drift kept apart (pure NumPy)
``quantities``    the quantity registry: what each quantity is, which kinds it supports, why the others are not offered
``measured``      measured records with integrity checks, and ``Unavailable``
``validation``    what the committed experiment artifacts say per (quantity, kind): serving tiers
``service``       ``PlayerIntelligenceService`` — one retrieval path for every consumer
``materialise``   store the registry's recommended models as candidates (a loop over R9.1's ``train_candidate``)
``promotion``     the promotion gates as code (reads only; promotes nothing)

The statistical harness (backtests, baselines, the candidate lifecycle) is ``app.modeling``; this
package builds on it rather than beside it. Nothing here changes a live rating. See ``docs/r9-2/``.
"""
