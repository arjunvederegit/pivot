"""R9.1 — evaluation and model infrastructure for player intelligence.

What lives here, and what deliberately does not:

* ``panel`` — the player-season panel and its **as-of views**. The only way feature code sees data
  is through an ``AsOfPanel``, which cannot contain a season after its information cutoff.
* ``targets`` — what a model is asked to predict, defined once, with units and sample-size meaning.
* ``predictors`` — transparent baselines and a small set of interpretable candidates.
* ``backtest`` / ``metrics`` — the rolling-origin harness and the statistics it reports.
* ``registry`` — candidate-only training: a candidate is a new ``model_versions`` row plus rows in
  ``candidate_estimates``. It never writes to, or deletes from, anything a serving path reads.
* ``provenance`` — data-snapshot fingerprints, environment capture, the feature manifest.
* ``contract`` — the typed envelope R9.2 and R9.3 will use to return player intelligence.

Nothing here changes a live rating. See ``docs/r9-1/``.
"""
