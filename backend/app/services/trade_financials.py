"""R9.3A — the financial picture of a proposed trade, and an honest account of what was checked.

Two things the Trade Evaluator did not previously say:

1. **The money.** Per traded player (cap hit, base salary, cash, contract years, source, freshness) and per
   team (known salary going out and coming in, the difference, the contract commitments it creates by
   season) — every figure carries the players it is *missing*, and a difference is stated only when every
   salary behind it is known. An unknown salary is never $0.

2. **The legality picture, inspectable.** The engine's rules each return ``pass`` / ``fail`` / ``warning`` /
   ``unavailable``. This module groups them into what was **checked and passed**, what **failed**, what could
   **not be verified**, and names the CBA restrictions Pivot does not implement at all. The verdict is one of

   * ``prohibited`` — at least one implemented rule fails (named);
   * ``indeterminate`` — nothing fails, but at least one rule could not be verified;
   * ``passes_checked_rules`` — every implemented rule passed. This is **never** "legal": the unchecked list
     below is always attached, because passing the checks Pivot has says nothing about the restrictions it
     does not have.

It is a read-side projection of the existing engine result: it changes no rule outcome and no basketball
evaluation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.cba.context import PlayerAsset, TeamContext, TradeContext
from app.services.financials import CAP_HIT_NOTE, NOT_ON_FILE, PlayerFinancials

#: Restrictions Pivot does not implement (docs/cba-rule-coverage.md, "Explicitly unsupported"), plus the
#: ones that need data no provider here supplies. Always attached to a legality assessment.
UNCHECKED_CATEGORIES: tuple[dict[str, str], ...] = (
    {"category": "Existing trade exceptions", "note": "Whether either team holds a traded-player exception, and its use, is not modelled."},
    {"category": "Sign-and-trade transactions", "note": "Sign-and-trade restrictions (including the hard cap they trigger) are not modelled."},
    {"category": "Cash considerations", "note": "Cash sent or received in the deal, and the annual limit on it, are not modelled."},
    {"category": "Base-year compensation", "note": "Recently re-signed players' salary-matching value (poison-pill provisions) is not modelled."},
    {"category": "Hard-cap triggers", "note": "Whether using an exception hard-caps a team at an apron is not modelled."},
    {"category": "Incentives and guarantees", "note": "Likely/unlikely incentives and non-guaranteed amounts, which change the salary counted in a trade, are not on file."},
    {"category": "Aggregation timing", "note": "Restrictions on re-trading recently acquired players need transaction dates Pivot does not hold."},
    {"category": "Pick legality", "note": "Pick swaps, protections and ownership beyond the Stepien check are not validated."},
    {"category": "Calendar restrictions", "note": "The trade deadline, moratoriums and the recently-signed-free-agent window are not checked against today's date."},
)

LEGALITY_STATEMENT = (
    "A rule that passes means only that rule passed. Pivot checks a subset of the CBA, so no result here is a "
    "statement that the trade is legal."
)


def _asset_row(asset: PlayerAsset) -> dict[str, Any]:
    fin: PlayerFinancials | None = asset.financials  # type: ignore[assignment]
    return {
        "player_id": asset.player_id,
        "name": asset.name,
        "from_team_id": asset.from_team_id,
        "to_team_id": asset.to_team_id,
        "financials": fin.as_dict() if fin else None,
        "contract_type_stated": asset.contract_type,
        "matching_class": asset.matching_class,
        "matching_class_basis": (
            "stated by the source"
            if asset.contract_type is not None
            else asset.not_two_way_basis
            if asset.matching_class
            else "unknown: the source states no type and the cap hit does not rule out a two-way contract"
        ),
    }


def _side(assets: list[PlayerAsset]) -> dict[str, Any]:
    known = [a for a in assets if a.salary is not None]
    unknown = [a.name for a in assets if a.salary is None]
    fins = [a.financials for a in assets if a.financials is not None]
    cash_known = all(f.current and f.current.total_cash is not None for f in fins) if fins else False
    base_known = all(f.current and f.current.base_salary is not None for f in fins) if fins else False
    complete = not unknown
    return {
        "players": [a.name for a in assets],
        "count": len(assets),
        "known_cap_hit_total": sum(a.salary or 0 for a in known) if known else None,
        "known_count": len(known),
        "unknown_players": unknown,
        "complete": complete and bool(assets),
        "cap_hit_total": sum(a.salary or 0 for a in assets) if assets and complete else None,
        "total_cash": sum((f.current.total_cash or 0) for f in fins if f.current) if assets and complete and cash_known else None,
        "base_salary_total": sum((f.current.base_salary or 0) for f in fins if f.current) if assets and complete and base_known else None,
    }


def _commitments(team: TeamContext, params_season: str, seasons: list[str]) -> list[dict[str, Any]]:
    """Per future season: the cap hits leaving and arriving, and whether each side is fully known."""
    rows = []
    for season in seasons:
        row: dict[str, Any] = {"season": season, "is_financial_season": season == params_season}
        for key, assets in (("outgoing", team.outgoing), ("incoming", team.incoming)):
            total = 0
            unknown: list[str] = []
            for asset in assets:
                fin: PlayerFinancials | None = asset.financials  # type: ignore[assignment]
                if fin is None or fin.status != "priced":
                    unknown.append(asset.name)
                    continue
                figures = next((f for f in fin.seasons if f.season == season), None)
                if figures is not None:
                    total += figures.cap_hit
                elif fin.end_status == "ends" and fin.final_season is not None and season > fin.final_season:
                    pass  # the contract is over: a known absence of commitment, not an unknown
                elif season == params_season:
                    unknown.append(asset.name)
                elif fin.end_status == "through_horizon" or fin.end_status == "ends":
                    pass  # within the loaded horizon and no row: contract not running that season
                else:
                    unknown.append(asset.name)  # length unknown: later seasons cannot be stated
            row[key] = {"known_total": total, "unknown_players": sorted(set(unknown)), "complete": not unknown}
        both = row["outgoing"]["complete"] and row["incoming"]["complete"]
        row["net_known"] = row["incoming"]["known_total"] - row["outgoing"]["known_total"] if both else None
        rows.append(row)
    return rows


def _team_block(team: TeamContext, context: TradeContext, seasons: list[str], legality_team: dict[str, Any]) -> dict[str, Any]:
    out, inc = _side(team.outgoing), _side(team.incoming)
    out_ok = not team.outgoing or out["complete"]
    inc_ok = not team.incoming or inc["complete"]
    net = None
    if (team.outgoing or team.incoming) and out_ok and inc_ok:
        net = (inc["cap_hit_total"] or 0) - (out["cap_hit_total"] or 0)
    ratio = None
    if out["complete"] and inc["complete"] and (out["cap_hit_total"] or 0) > 0:
        ratio = round((inc["cap_hit_total"] or 0) / out["cap_hit_total"], 4)
    note = None
    if net is None and (team.outgoing or team.incoming):
        missing = out["unknown_players"] + inc["unknown_players"]
        note = (
            "Salary difference not stated: no cap hit is on file for " + ", ".join(missing)
            + ". Unknown salaries are not counted as $0."
        )
    coverage_before = team.coverage_before
    coverage_after = team.coverage_after
    return {
        "team_id": team.team_id,
        "abbreviation": team.abbreviation,
        "name": team.name,
        "outgoing": out,
        "incoming": inc,
        "net_salary_change": net,
        "incoming_to_outgoing_ratio": ratio,
        "net_salary_note": note,
        "known_subtotal_before": coverage_before.known if coverage_before else None,
        "known_subtotal_after": coverage_after.known if coverage_after else None,
        "coverage_before": coverage_before.as_dict() if coverage_before else None,
        "coverage_after": coverage_after.as_dict() if coverage_after else None,
        "subtotal_label": "Payroll" if coverage_before and coverage_before.complete else "Known salary subtotal",
        "apron_status_at_least_after": legality_team.get("apron_status_at_least_after"),
        "apron_status_after": legality_team.get("apron_status_after"),
        "commitments_by_season": _commitments(team, context.league_year, seasons),
        "matching_eligible": {
            "outgoing": team.outgoing_salary,
            "incoming": team.incoming_salary,
            "note": "Two-way contracts are excluded from salary matching; this is the matching-eligible sum, "
            "and is withheld when a contract's type cannot be established.",
        },
    }


# ------------------------------------------------------------------------------ legality


_RULE_TITLES = {
    "SALARY_DATA_AVAILABLE": "Salary data on file",
    "SALARY_MATCHING": "Salary matching",
    "SECOND_APRON_AGGREGATION": "Second-apron aggregation",
    "MINIMUM_TEAM_SALARY": "Minimum team salary",
    "ROSTER_SIZE": "Roster size",
    "RECENTLY_SIGNED": "Recently signed players",
    "NO_TRADE_CLAUSE": "No-trade clauses",
    "TWO_WAY_EXCLUSION": "Two-way contracts",
    "STEPIEN_FUTURE_FIRSTS": "Future first-round picks (Stepien)",
}


def legality_assessment(legality: dict[str, Any], team_names: dict[str, str]) -> dict[str, Any]:
    """Group the engine's rule results into checked / failed / unverified, with a conservative verdict."""
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in legality["rule_results"]:
        by_rule[result["rule_code"]].append(result)

    checked: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for code, results in by_rule.items():
        statuses = {r["status"] for r in results}
        if "fail" in statuses:
            aggregate = "fail"
        elif "unavailable" in statuses:
            aggregate = "unavailable"
        elif "warning" in statuses:
            aggregate = "warning"
        else:
            aggregate = "pass"
        entries = []
        for r in results:
            entry = {
                "rule_code": code,
                "title": _RULE_TITLES.get(code, code),
                "team_id": r["team_id"],
                "team": team_names.get(r["team_id"] or "", "both teams"),
                "status": r["status"],
                "message": r["message"],
                "confidence": r.get("confidence", "high"),
                "source_reference": r.get("source_reference", ""),
                "calculation": r.get("calculation", {}),
            }
            entries.append(entry)
            if r["status"] == "pass":
                checked.append(entry)
            elif r["status"] == "fail":
                failed.append(entry)
            else:  # unavailable and warning: neither a verified pass nor a verified failure
                unverified.append(entry)
        rules.append({"rule_code": code, "title": _RULE_TITLES.get(code, code), "status": aggregate, "results": entries})

    indications = [
        {
            "rule_code": u["rule_code"],
            "title": u["title"],
            "team": u["team"],
            "explanation": u["calculation"]["indicated_failure"]["explanation"],
            "confidence": "medium",
        }
        for u in unverified
        if isinstance(u["calculation"].get("indicated_failure"), dict)
    ]
    if failed:
        names = sorted({f["title"] for f in failed})
        verdict = "prohibited"
        headline = "Prohibited under " + ", ".join(names) + " (a checked rule fails; the failure stands whatever else is unverified)."
        if any(f["confidence"] != "high" for f in failed):
            headline += (
                " Confidence is medium: the payroll floor behind it comes from the current roster snapshot, "
                "which may still hold camp contracts."
            )
    elif unverified:
        verdict = "indeterminate"
        headline = (
            f"Indeterminate: {len(unverified)} check(s) could not be verified from the data on file; "
            "no checked rule fails."
        )
        if indications:
            teams = ", ".join(sorted({i["team"] for i in indications}))
            headline = (
                f"Indeterminate — but the known salaries alone already indicate a {indications[0]['title'].lower()} "
                f"problem for {teams} (not verified: payroll is incomplete)."
            )
    elif checked:
        verdict = "passes_checked_rules"
        headline = "Passes every rule Pivot checks — not a statement that the trade is legal."
    else:
        verdict = "indeterminate"
        headline = "Indeterminate: no rule could be evaluated."
    return {
        "verdict": verdict,
        "headline": headline,
        "statement": LEGALITY_STATEMENT,
        "counts": {"checked_passed": len(checked), "failed": len(failed), "unverified": len(unverified)},
        "indications": indications,
        "checked": checked,
        "failed": failed,
        "unverified": unverified,
        "rules": rules,
        "unchecked_categories": list(UNCHECKED_CATEGORIES),
        "engine_status": legality["overall_status"],
    }


def trade_financial_analysis(
    context: TradeContext, legality: dict[str, Any], *, roster_basis: dict[str, Any] | None = None, stats_label: str | None = None
) -> dict[str, Any]:
    """The financial block returned beside the engine result (additive; nothing in it feeds a verdict)."""
    moved: list[PlayerAsset] = []
    seen: set[str] = set()
    for team in context.teams:
        for asset in team.outgoing + team.incoming:
            if asset.player_id not in seen:
                seen.add(asset.player_id)
                moved.append(asset)

    horizon_seasons: list[str] = []
    for asset in moved:
        fin: PlayerFinancials | None = asset.financials  # type: ignore[assignment]
        if fin is not None:
            for figures in fin.seasons:
                if figures.season not in horizon_seasons:
                    horizon_seasons.append(figures.season)
    horizon_seasons.sort()
    if context.league_year not in horizon_seasons:
        horizon_seasons.insert(0, context.league_year)

    team_names = {t.team_id: t.abbreviation for t in context.teams}
    teams = {
        t.team_id: _team_block(t, context, horizon_seasons, legality["teams"].get(t.team_id, {}))
        for t in context.teams
    }
    params = context.params
    return {
        "financial_season": context.league_year,
        "season_basis": {
            "financial": context.league_year,
            "roster": (roster_basis or {}).get("season"),
            "roster_is_provisional": bool((roster_basis or {}).get("provisional")),
            "statistics": stats_label,
            "note": (
                "Salaries are for the financial season; the roster is the current roster snapshot; "
                "performance evidence comes from the statistics season. They can differ."
            ),
        },
        "thresholds": {
            "season": params.league_year,
            "salary_cap": params.salary_cap,
            "luxury_tax": params.luxury_tax,
            "first_apron": params.first_apron,
            "second_apron": params.second_apron,
            "source_name": params.source_name,
        },
        "definitions": CAP_HIT_NOTE,
        "players": [_asset_row(a) for a in moved],
        "teams": teams,
        "not_on_file": NOT_ON_FILE,
        "legality_assessment": legality_assessment(legality, team_names),
        "contract_provider_configured": context.contract_provider_configured,
    }
