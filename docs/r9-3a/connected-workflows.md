# Connected workflows — one player, carried between tools

R9.3A implements the smallest mechanism that lets an investigation move between tools without being re-entered: a **link that carries canonical identifiers and the period being read, and
nothing else**. It is deliberately not a workspace, a global store, or an investigation graph (those are R9.3B).

## 1. The two links

```
Player Explorer ──▶ /trade-evaluator?team=<team id>&player=<player id>&from=player-explorer&stats=<season>
Trade Evaluator ──▶ /players/<player id>?from=trade-evaluator
```

`frontend/lib/investigation.ts` is the only place these are built and parsed.

| Parameter | What it is | Why it travels |
| --- | --- | --- |
| `player` | the **canonical Pivot player id** (a UUID — never a provider id, an NBA.com id or a name) | so the receiving tool needs no search |
| `team` | the team the player is on **in the roster snapshot the user saw** | so the builder opens on that roster |
| `stats` | the **statistics season** behind the evidence on screen (e.g. `2025-26`) | so a season difference can be named instead of hidden |
| `from` | `player-explorer` or `trade-evaluator` | the receiving tool says where the context came from |

Anything else is refused: a value that is not a canonical id is dropped rather than echoed into a URL or a request (`investigation.test.ts`: provider ids, names and path-like strings return `null`).
**No number travels through a URL.** Salary, cap hit, TEI and every statistic are re-read from the backend by the receiving tool, so no stale figure can be carried silently.

## 2. Player Explorer → Trade Evaluator

1. The player file's primary action is **"Evaluate *Name* in a trade"** (the team-level "Open BOS in Trade Evaluator" stays beside it; the Contract tab links too).
2. The Trade Evaluator opens on that team's roster, exactly as the existing `?team=` flow does — the initial team-selection behaviour is unchanged (`team` is still honoured on its own).
3. A **"Carried from Player Explorer"** notice *verifies* the context against the roster the evaluator itself loaded:
   * found → "*Name* is on the BOS roster with a 2026-27 cap hit of $58.5M. Choose the team he would go to and he is placed in the deal." — and if the salary season and the statistics season differ,
     "Seasons differ: the salary is for 2026-27; the performance evidence you were reading is from 2025-26.";
   * not found (stale link, player moved) → a warning that the context was **not applied**, naming why. Nothing is silently carried.
4. The first partner team the user adds receives the player automatically — **only** if he is verifiably on the origin roster in the evaluator's own data.
5. The deal then behaves like any other: live rules check, salary and contracts, evaluation.

## 3. Trade Evaluator → Player Explorer, without losing the scenario

Every traded player's name (the salary table, the roster drawer's "Open full player file") links to his player file with `from=trade-evaluator`, where a **"Back to your trade"** button appears.

The scenario survives the round trip because the Trade Evaluator now **keeps the deal in its address bar** (`history.replaceState`, no navigation, no new route): the existing share-state encoding
(`?state=…`, the same one "Copy share link" produces) is updated as teams, moves, picks and the deal name change, and transient link parameters are consumed into it. Browser Back therefore
re-seeds the same deal. `load=` is dropped once applied so a restored deal is never overwritten by the saved trade it started from.

## 4. How R9.3B could extend it

The four parameters are the first fields of the `InvestigationContext` proposed in `docs/r9-2/R9-3-IMPLEMENTATION-RECOMMENDATION.md` §2.1. R9.3B can:

* add `q` (quantities), `mv` (pinned model versions) and `as_of` so a shared link reproduces the same experimental numbers (versions are hash-named and append-only);
* return the context from one backend route (`GET /player-intelligence/context/{player_id}`) instead of reconstructing it in each tool;
* carry a `scenario_id` so Strategy Lab and the Trade Evaluator share a scenario;
* let Team Outlook and Ask Pivot open the same context.

None of that is needed for R9.3A and none of it was started.

## 5. Cross-tool consistency (verified)

For the same player, Player Explorer, Trade Evaluator and Cap Center agree on: canonical player id, team and current-roster context, financial season (2026-27), cap hit, contract length, and
provider provenance, because all three read `app/services/financials.py`. Pinned by `test_the_three_surfaces_agree_on_a_salary` and re-verified in the browser against real rosters
(`qa-and-regression-report.md`, workflow E).

## 6. Limits

The carried player is applied only when the evaluator's own roster data confirms him; a player with no current team on the roster snapshot cannot be carried (the button is absent). The deal lives in
the URL, not on the server, so it does not follow the user across browsers except through the existing share link.
