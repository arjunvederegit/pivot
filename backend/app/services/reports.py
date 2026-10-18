"""The decision memo: one document a front office can review a trade from.

Deterministic. Every number comes from the evaluation and legality engines; optional LLM
enhancement may only rewrite prose and never calculates a value or adds a fact.

## What R6 changed, and why

The R1 report was an executive summary of the *composite*: a verdict, three component
scores, a wins line, some risks. It answered "what did the model say" and left the reader
to ask everything else somewhere else. R6's brief was a decision artifact, so the memo now
carries the five things a person reviewing a basketball decision asks next and the product
already knew:

- **what happens to the rotation** — which roles get congested, which are lost, whose
  minutes move (R6-4);
- **what the deal costs in draft capital** — the priced picks, and the ones this product
  refuses to price, each with the reason (R5-2);
- **precedent** — the completed trades that most resemble this one, with what makes them
  similar and the explicit statement that resemblance is not consequence (R6-2);
- **what is not known** — one consolidated section rather than caveats scattered through
  nine others;
- **the fit, in the terms the model actually used** — which needs the deal addresses, what
  it duplicates, and the needs no player skill claims to address at all (R4-2).

**It is not a dump of every number.** The rotation section reports the roles that moved by
more than `MATERIAL_MINUTES`, not all fourteen. The comparables section shows three, not
twenty-five. Components are ranked by distance from neutral and the top three are
discussed. Everything else remains available through the API, which is where a number
nobody asked for belongs.
"""

from datetime import UTC, datetime
from typing import Any

import markdown as md_lib

from app.config import get_settings

#: A role whose minutes move by less than this is not a roster consequence, it is
#: allocator noise. Reporting all fourteen roles turned the section into a table nobody
#: reads.
MATERIAL_MINUTES = 2.0
MAX_COMPARABLES = 3


def _fmt_money(value: int | None) -> str:
    return f"${value:,.0f}" if value is not None else "unavailable"


def _fmt_score(value: float | None) -> str:
    return f"{value:.0f}/100" if value is not None else "unavailable"


LEGALITY_LABELS = {
    "verified_legal": "Verified legal (all implemented rules passed with current data)",
    "verified_illegal": "Verified illegal (at least one implemented rule failed)",
    "conditionally_valid": "Conditionally valid (implemented rules passed; some required data unavailable)",
    "not_evaluated": "Not evaluated (insufficient data)",
}


def build_report_markdown(
    trade_name: str,
    focal_team_name: str,
    strategy: str,
    legality: dict,
    evaluations: dict[str, dict],
    focal_team_id: str,
    alternatives: list[dict] | None = None,
    data_freshness: dict | None = None,
    comparables: dict | None = None,
) -> str:
    settings = get_settings()
    focal = evaluations.get(focal_team_id, {})
    components = focal.get("components", {})
    uncertainty = focal.get("uncertainty", {})
    detail = focal.get("detail", {})
    drivers = sorted(
        ({"k": k, "v": v} for k, v in components.items() if v is not None),
        key=lambda d: abs(d["v"] - 50),
        reverse=True,
    )
    utility = focal.get("composite_utility")
    status = legality.get("overall_status", "not_evaluated")
    perf = detail.get("performance", {})
    excluded = focal.get("excluded_components", [])
    suppression = focal.get("suppression") or {}
    decision_status = focal.get("decision_status", "scored")

    if decision_status == "suppressed_illegal":
        verdict = "Do not proceed — the trade fails implemented CBA rules"
    elif utility is None:
        verdict = "No recommendation — this deal could not be scored"
    elif utility >= 55:
        verdict = "Proceed with further diligence"
    elif utility >= 45:
        verdict = "Neutral — depends on strategic priorities"
    else:
        verdict = "Do not proceed as constructed"

    lines: list[str] = []
    unknowns: list[str] = []

    lines.append(f"# Decision memo — {trade_name}")
    lines.append("")
    lines.append(
        f"*Prepared for {focal_team_name} · strategy: {strategy} · "
        f"generated {datetime.now(UTC).strftime('%B %d, %Y %H:%M UTC')}*"
    )
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    if decision_status == "suppressed_illegal":
        lines.append(f"**{verdict}.**")
        lines.append("")
        lines.append(
            "No composite utility is reported: a deal that fails a verified rule cannot "
            "be executed, so scoring it would invite comparing it against deals that can."
        )
        lines.append("")
        for rule in suppression.get("failing_rules", [])[:6]:
            lines.append(f"- **{rule['rule_code']}** — {rule['message']}")
        lines.append("")
    else:
        lines.append(
            f"**{verdict}.** Composite utility for {focal_team_name}: "
            f"**{_fmt_score(utility)}** (confidence: {focal.get('confidence', 'unknown')})."
        )
        lines.append("")
        for d in drivers[:3]:
            direction = "strengthens" if d["v"] >= 50 else "weakens"
            lines.append(
                f"- The deal {direction} the **{d['k']}** dimension ({_fmt_score(d['v'])})."
            )
        if not drivers:
            lines.append("- No component could be scored with the data currently available.")
        lines.append("")
    lines.append(f"Legality: **{LEGALITY_LABELS.get(status, status)}**")
    lines.append("")

    _floor_section(lines, focal, perf, uncertainty, detail, unknowns)
    _fit_section(lines, detail, unknowns)
    _cost_section(lines, focal, detail, unknowns)
    _rules_section(lines, legality, focal_team_id, unknowns)
    _precedent_section(lines, comparables, unknowns)
    _risk_section(lines, uncertainty, detail, unknowns)

    lines.append("## What is not known")
    lines.append("")
    if excluded:
        unknowns.append(
            f"Components excluded for missing data, with the weights renormalized: "
            f"{', '.join(excluded)}."
        )
    unmodeled = focal.get("unmodeled_players") or []
    if unmodeled:
        unknowns.append(
            f"{len(unmodeled)} player(s) on this roster or in this deal have no impact "
            f"estimate and are excluded from the projection rather than defaulted: "
            f"{', '.join(unmodeled[:8])}"
            + (" …" if len(unmodeled) > 8 else "")
            + "."
        )
    if not unknowns:
        unknowns.append("Nothing this memo relies on was unavailable.")
    for note in unknowns:
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## Alternatives considered")
    lines.append("")
    if alternatives:
        for alt in alternatives[:5]:
            lines.append(
                f"- **{alt['name']}** — utility {_fmt_score(alt.get('composite_utility'))}, "
                f"legality: {alt.get('legality_status', 'unknown')}"
            )
    else:
        lines.append(
            "- No saved alternatives in this comparison. Use the comparison view to add 2–5."
        )
    lines.append("")

    lines.append("## Questions this product cannot answer")
    lines.append("")
    lines.append(
        "- Are the incoming players' medicals acceptable? (This tool does not model injuries.)"
    )
    lines.append(
        "- Does the role fit survive contact with the coaching staff's actual rotation?"
    )
    lines.append("- Would the other front office accept this? Nothing here asked them.")
    if status == "conditionally_valid":
        lines.append(
            "- Verify salary matching with authoritative contract data before any commitment."
        )
    lines.append("")

    lines.append("## Assumptions and provenance")
    lines.append("")
    lines.append(
        "- Player impact uses Pivot Estimated Impact (TEI), this project's own "
        "portfolio-model estimate with documented validation — not a proprietary metric."
    )
    lines.append(
        "- Post-trade minutes are reallocated by the rotation model against the pre-trade "
        "allocation; a coach's actual rotation will differ."
    )
    freshness = data_freshness or {}
    lines.append(
        f"- Source: {freshness.get('source', 'unknown')} · rosters/stats last synced: "
        f"{freshness.get('last_sync', 'unknown')} · rosters {settings.roster_season_preferred} · "
        f"statistics {settings.current_season}."
    )
    lines.append(
        f"- Cap parameters: {legality.get('cap_parameters_source', 'unknown')} "
        f"(league year {legality.get('league_year')})."
    )
    lines.append(
        "- This is an analytical portfolio project, not an official NBA cap-management "
        "product; legality coverage is a documented subset of the CBA "
        "(see docs/cba-rule-coverage.md)."
    )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- sections


def _named(players: list[dict]) -> str:
    """A player with no impact estimate is named without a fabricated TEI."""
    return (
        ", ".join(
            f"{p['name']} (TEI {p['tei']:+.1f})"
            if p.get("tei") is not None
            else f"{p['name']} (impact not modelled)"
            for p in players
        )
        or "none"
    )


def _floor_section(
    lines: list[str],
    focal: dict,
    perf: dict,
    uncertainty: dict,
    detail: dict,
    unknowns: list[str],
) -> None:
    lines.append("## 1. What changes on the floor")
    lines.append("")
    if perf.get("delta_wins") is not None:
        lines.append(
            f"- Projected regular-season impact: **{perf['delta_wins']:+.1f} wins** "
            f"(net-rating change {perf.get('delta_net_rating', 0):+.2f}, converted at "
            f"{perf.get('wins_mapping', {}).get('slope', 0):.2f} wins per point, calibrated on "
            f"{perf.get('wins_mapping', {}).get('n', '?')} team-seasons)."
        )
    elif perf.get("unavailable"):
        unknowns.append(f"Projection: {perf['unavailable']}.")
    if uncertainty.get("prob_positive") is not None:
        lines.append(
            f"- Uncertainty (Monte Carlo, {uncertainty.get('n_draws', 0)} draws): median "
            f"{uncertainty.get('median', 0):+.1f} wins, 10th–90th percentile "
            f"[{uncertainty.get('p10', 0):+.1f}, {uncertainty.get('p90', 0):+.1f}], "
            f"P(positive) = {uncertainty['prob_positive']:.0%}."
        )
    elif uncertainty.get("unavailable"):
        unknowns.append(f"Outcome distribution: {uncertainty['unavailable']}.")
    lines.append(f"- Incoming: {_named(focal.get('incoming', []))}")
    lines.append(f"- Outgoing: {_named(focal.get('outgoing', []))}")

    shape = detail.get("roster_shape") or {}
    if shape.get("unavailable"):
        unknowns.append(f"Rotation shape: {shape['unavailable']}.")
    elif shape.get("roles"):
        moved = [r for r in shape["roles"] if abs(r["delta"]) >= MATERIAL_MINUTES]
        if moved:
            lines.append("")
            lines.append("**Rotation consequences** (minutes by role, of 240):")
            lines.append("")
            lines.append("| Role | Before | After | League median | Note |")
            lines.append("| --- | ---: | ---: | ---: | --- |")
            for role in sorted(moved, key=lambda r: -abs(r["delta"])):
                note = (
                    "above the league 90th percentile"
                    if role["congested"]
                    else "no longer a rotation role"
                    if role["lost"]
                    else ""
                )
                median = (
                    f"{role['league_median']:.0f}" if role["league_median"] is not None else "—"
                )
                lines.append(
                    f"| {role['role']} | {role['minutes_before']:.0f} | "
                    f"{role['minutes_after']:.0f} | {median} | {note} |"
                )
        if shape.get("lineup_fit", {}).get("available") is False:
            unknowns.append(
                "Lineup-aware fit is unavailable: "
                f"{shape['lineup_fit']['reason']}. Re-check with "
                f"`{shape['lineup_fit']['recheck']}`."
            )
    lines.append("")


def _fit_section(lines: list[str], detail: dict, unknowns: list[str]) -> None:
    fit = detail.get("fit") or {}
    lines.append("## 2. Does it fit this roster")
    lines.append("")
    if fit.get("unavailable"):
        lines.append(f"- Not assessed: {fit['unavailable']}.")
        unknowns.append(f"Roster fit: {fit['unavailable']}.")
        lines.append("")
        return
    addressed = sorted(
        (fit.get("needs_addressed") or {}).items(), key=lambda kv: -abs(kv[1])
    )
    if addressed:
        for need, contribution in addressed[:3]:
            direction = "addresses" if contribution > 0 else "worsens"
            lines.append(
                f"- The deal {direction} **{need.replace('_', ' ')}** "
                f"({contribution:+.3f} of the fit score)."
            )
    else:
        lines.append("- No measured need is moved by the players in this deal.")
    redundancies = fit.get("redundancies") or {}
    if redundancies:
        worst = max(redundancies.items(), key=lambda kv: kv[1])
        lines.append(
            f"- It duplicates strength the roster already has in "
            f"**{worst[0].replace('_', ' ')}** (redundancy {worst[1]:.3f}), which the fit "
            "score charges for."
        )
    withheld = fit.get("needs_not_addressable") or {}
    for need, reason in list(withheld.items())[:2]:
        unknowns.append(
            f"**{need.replace('_', ' ')}** is a measured weakness that no player skill "
            f"claims to address — {reason}."
        )
    if fit.get("baseline_note"):
        lines.append(f"- One side of this deal is empty: {fit['baseline_note']}.")
    not_compared = fit.get("skills_not_compared") or []
    if not_compared:
        unknowns.append(
            "Skills measured on only one side of the deal, so not compared: "
            f"{', '.join(s.replace('_', ' ') for s in not_compared)}."
        )
    lines.append("")


def _cost_section(lines: list[str], focal: dict, detail: dict, unknowns: list[str]) -> None:
    lines.append("## 3. What it costs")
    lines.append("")
    team_legality = focal.get("legality", {})
    assets = detail.get("assets") or {}
    if team_legality.get("payroll_after") is not None:
        lines.append(
            f"- Post-trade payroll: {_fmt_money(team_legality['payroll_after'])} "
            f"(apron status: {team_legality.get('apron_status_after', 'unknown')})."
        )
        lines.append(
            f"- Salary in: {_fmt_money(team_legality.get('incoming_salary'))}; "
            f"salary out: {_fmt_money(team_legality.get('outgoing_salary'))}."
        )
    elif assets.get("payroll_delta") is not None:
        lines.append(
            f"- Payroll change on the players moved: "
            f"{_fmt_money(assets['payroll_delta'])} ({assets.get('payroll_basis', '')})."
        )
        unknowns.append(
            "Total post-trade payroll is unavailable: it needs a salary for every "
            "rostered player, which the configured contract provider does not supply."
        )
    else:
        unknowns.append(
            "Contract data is unavailable from the configured provider, so payroll, tax "
            "and apron consequences cannot be verified."
        )

    priced = assets.get("picks_priced") or []
    unpriced = assets.get("picks_not_priced") or []
    if priced:
        lines.append(
            "- Draft capital: "
            + "; ".join(
                f"{row['pick']} {row['direction']} "
                f"({row.get('point', 0):.1f} pick points"
                + (
                    f", {row['low']:.1f}–{row['high']:.1f}"
                    if row.get("low") is not None and row.get("high") is not None
                    else ""
                )
                + ")"
                for row in priced[:4]
            )
            + f" — net {assets.get('pick_units_net', 0):+.2f} reference picks."
        )
    for row in unpriced[:3]:
        unknowns.append(
            f"Pick {row['pick']} ({row['direction']}) has no point value: "
            f"{row.get('precision', 'unknown')} — "
            f"{'; '.join(row.get('unresolved', []) or ['conditional terms'])}."
        )
    if not priced and not unpriced:
        lines.append("- No draft picks move in this deal.")
    elif not priced:
        lines.append(
            f"- Draft capital: {len(unpriced)} pick(s) move and none can be priced — "
            "each is named under *What is not known* with the reason."
        )
    # A section that renders empty reads as "nothing to say here", which is the opposite
    # of what an unpriceable deal means.
    if not any(line.startswith("- ") for line in lines[lines.index("## 3. What it costs") :]):
        lines.append(
            "- Nothing about this deal's cost could be established from the data on "
            "file. See *What is not known*."
        )
    lines.append("")


def _rules_section(
    lines: list[str], legality: dict, focal_team_id: str, unknowns: list[str]
) -> None:
    results = [
        r
        for r in legality.get("rule_results", [])
        if r.get("team_id") in (None, focal_team_id)
    ]
    failed = [r for r in results if r.get("status") == "fail"]
    warnings = [r for r in results if r.get("status") == "warning"]
    unavailable = [r for r in results if r.get("status") == "unavailable"]
    lines.append("## 4. Rules")
    lines.append("")
    if failed:
        for rule in failed[:5]:
            lines.append(f"- **FAIL — {rule['rule_code']}**: {rule['message']}")
    for rule in warnings[:4]:
        lines.append(f"- Warning ({rule['rule_code']}): {rule['message']}")
    if not failed and not warnings:
        lines.append("- No implemented rule fails or warns on this deal for this team.")
    if unavailable:
        unknowns.append(
            f"{len(unavailable)} implemented CBA check(s) could not reach a verdict: "
            + "; ".join(f"{r['rule_code']} ({r['message']})" for r in unavailable[:3])
            + "."
        )
    lines.append("")


def _precedent_section(lines: list[str], comparables: dict | None, unknowns: list[str]) -> None:
    lines.append("## 5. Precedent")
    lines.append("")
    if comparables is None:
        lines.append("- Comparable trades were not requested for this memo.")
        lines.append("")
        return
    if not comparables.get("available"):
        reason = comparables.get("unavailable_reason", "no reason given")
        lines.append(f"- No comparable trades: {reason}.")
        unknowns.append(f"Historical precedent: {reason}.")
        lines.append("")
        return
    coverage = comparables.get("coverage", {})
    for row in comparables.get("comparables", [])[:MAX_COMPARABLES]:
        lines.append(
            f"- **{row['team_abbreviation']}, {row.get('transaction_date', 'unknown date')}** "
            f"({row['similarity']:.0%} similar) — {row.get('source_text', '')}"
        )
        for why in (row.get("why") or [])[:2]:
            lines.append(f"    - {why}")
    lines.append("")
    # `trades_rankable`, not `trades_ingested`. 565 trades are ingested and 535 can be
    # ranked; naming the larger number in a document a front office reviews claims coverage
    # the retrieval does not have. The same line in the UI panel was corrected with it.
    ingested = coverage.get("trades_ingested", 0)
    rankable = coverage.get("trades_rankable", 0)
    unrankable = max(0, ingested - rankable)
    lines.append(
        f"Drawn from {coverage.get('sides_rankable', 0):,} rankable sides of {rankable:,} "
        f"completed trades"
        + (
            f"; {unrankable:,} of the {ingested:,} ingested cannot be ranked, because this "
            "database holds no production for the season that would describe them"
            if unrankable
            else ""
        )
        + ". **Resemblance is not consequence**: nothing in the retrieval reads what "
        "happened after these trades, and a historical deal that worked is not an argument "
        "that this one will."
    )
    not_scored = comparables.get("not_scored") or []
    if not_scored:
        unknowns.append(
            "Comparable retrieval does not score "
            + ", ".join(entry["field"] for entry in not_scored)
            + " — see the API response for the reason on each."
        )
    lines.append("")


def _risk_section(
    lines: list[str], uncertainty: dict, detail: dict, unknowns: list[str]
) -> None:
    risk = detail.get("risk") or {}
    lines.append("## 6. Risks")
    lines.append("")
    wrote = False
    if uncertainty.get("prob_positive") is not None:
        lines.append(
            f"- {1 - uncertainty['prob_positive']:.0%} of simulations produce a negative "
            "win impact."
        )
        wrote = True
    if risk.get("incoming_availability") is not None:
        lines.append(
            f"- Historical availability of incoming players: "
            f"{risk['incoming_availability']:.0%} of team games "
            "(availability is historical games played, not a medical prediction)."
        )
        wrote = True
    if risk.get("availability_delta") is not None:
        delta = risk["availability_delta"]
        direction = "sheds" if delta > 0 else "takes on"
        lines.append(
            f"- Availability exposure: the deal {direction} {abs(delta):.1%} of games "
            "on the minutes involved (minutes-weighted, arriving minus departing). This "
            "is the entire risk component; the projection's own uncertainty is reported "
            "in §1 and deliberately not scored a second time."
        )
        wrote = True
    verification = risk.get("legality_verification") or {}
    if verification.get("share") is not None:
        lines.append(
            f"- {verification['rules_with_a_definite_verdict']} of "
            f"{verification['rules_evaluated']} implemented CBA checks reached a verdict "
            "for this team. Reported, never scored."
        )
        wrote = True
    if risk.get("unavailable"):
        unknowns.append(f"Risk: {risk['unavailable']}.")
    if not wrote:
        lines.append("- No risk term could be measured for this deal.")
    lines.append("")


def report_to_html(markdown_text: str) -> str:
    body = md_lib.markdown(markdown_text, extensions=["tables"])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Pivot decision memo</title>
<style>
body {{ font-family: Georgia, serif; max-width: 780px; margin: 40px auto; padding: 0 20px; color: #1a1a2e; }}
h1 {{ font-size: 1.6rem; border-bottom: 2px solid #1a1a2e; padding-bottom: 8px; }}
h2 {{ font-size: 1.1rem; margin-top: 28px; }}
li {{ margin: 4px 0; }}
em {{ color: #555; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 0.92rem; }}
th, td {{ border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
th {{ background: #f5f5f7; }}
td:nth-child(2), td:nth-child(3), td:nth-child(4) {{ text-align: right; }}
@media print {{ body {{ margin: 12px; }} }}
</style></head><body>{body}</body></html>"""


def memo_payload(markdown_text: str, sections: list[str] | None = None) -> dict[str, Any]:
    """The memo as data, for a client that wants to render it itself."""
    return {
        "markdown": markdown_text,
        "sections": sections
        or [line[3:] for line in markdown_text.splitlines() if line.startswith("## ")],
        "generated_at": datetime.now(UTC).isoformat(),
    }
