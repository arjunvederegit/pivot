"""R9.2 — the reusable Player Intelligence Engine.

``components``    integer makes/attempts recovered from what the provider stored
``panel``         R9.1's player-season panel plus those counts
``rate_models``   sampling noise, ability and drift kept apart (pure NumPy)
``records``       the typed ``PlayerIntelligenceRecord`` builders (measured / inferred / projected)
``service``       ``PlayerIntelligenceService`` — one retrieval path for every consumer

The statistical harness (backtests, baselines, the candidate lifecycle) is ``app.modeling``; this
package builds on it rather than beside it. Nothing here changes a live rating. See ``docs/r9-2/``.
"""
