"""Player and roster intelligence: the read surface Pivot's decision workflow starts from.

The shipped product could already answer "what does this trade do?". It could not answer
the three questions that come *before* a trade, even though it held every number needed:

    what does this player actually do?          -> player_intelligence()
    what does our roster contain, and lack?     -> team_profile()
    would this player fit HERE?                 -> player_team_fit()

Nothing in this module computes a new basketball quantity. Every number it returns is
produced by machinery that already shipped and is already tested — `player_skill_vector`,
the deterministic archetype chain, the stored `team_needs` rows, and `EvaluationService._fit`
with its measured one-way baselines. What is new is that these are *addressable*: a caller
can ask about one player or one team without constructing a trade first.

Three properties this module is responsible for, none of which the underlying machinery
enforces on its own:

**Fit is conditional, and the type makes it so.** There is no `fit(player)`. The only entry
point is `player_team_fit(db, player_id, team_id)`, because Fit(X, A) != Fit(X, B) is the
product's position and a signature that permitted a universal score would undermine it in
the one place a reader looks first.

**A dimension Pivot cannot see is named, not omitted.** `player_intelligence` returns an
entry for every dimension in `domain.skills.DECLARED_DIMENSIONS`, and the ones with no
measurement carry `available: false` and the reason. A reader learns that Pivot cannot see
switchability rather than concluding that switchability does not matter.

**Strength and weakness are decided here, once.** The thresholds that turn a need row into
a headline used to be constants in the browser (`frontend/lib/needs.ts`), which made a
basketball judgement a presentation-layer decision no backend test could reach. They live
in `domain.needs` now and are applied here, so every client is served one answer.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import Player, PlayerArchetype, Team, TeamNeed
from app.domain import archetypes as domain_archetypes
from app.domain import needs as domain_needs
from app.domain import skills as domain_skills
from app.domain.evidence import Confidence, Evidence, Measurement
from app.services.evaluation import EvaluationService, PlayerCard

#: The population every skill percentile is taken against, stated wherever one is served.
#: It names the *population*, not the upstream: the same percentile is computed over
#: whatever rows were ingested, so asserting NBA.com here labelled demo and CSV-derived
#: skills as NBA.com data. Row-level provenance is served by `Provenance`.
SKILL_SOURCE = "league percentile of the recency-weighted feature window"


class IntelligenceService:
    """Read-only intelligence over one league snapshot.

    Wraps `EvaluationService` rather than re-implementing it: the league skill frame, the
    roster cards and the fit machinery are expensive and already correct, and a second
    implementation of any of them is a second thing to keep true.

    Construct one per request. The underlying service caches the league skill frame in
    Redis for six hours under a schema fingerprint, so the cost is paid once per ingestion
    rather than once per call.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._eval = EvaluationService(db)

    # ------------------------------------------------------------------ vocabulary

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        """What Pivot claims to measure, and what it has declared it cannot.

        Served so the methodology surface is generated from the same records the engines
        read, rather than from prose that can drift away from them.
        """
        return {
            "skills": [
                {
                    "key": d.key,
                    "label": d.label,
                    "side": d.side.value,
                    "definition": d.definition,
                    "available": d.available,
                    "method": d.method,
                    "unavailable_reason": d.unavailable_reason,
                    "evidence": d.evidence.value,
                    "confidence": d.confidence.value,
                    "limitations": list(d.limitations),
                }
                for d in domain_skills.DECLARED_DIMENSIONS
            ],
            "archetypes": [
                {
                    "key": a.key,
                    "label": a.label,
                    "family": a.family.value,
                    "definition": a.definition,
                    "contributes": list(a.contributes),
                    "role_id": a.role_id,
                }
                for a in domain_archetypes.CATALOG
            ],
            "needs": [
                {
                    "key": n.key,
                    "label": n.label,
                    "source": n.source.value,
                    "definition": n.definition,
                    "addressed_by": n.addressed_by,
                    "unaddressable_reason": n.unaddressable_reason,
                    "proxy_note": n.proxy_note,
                }
                for n in domain_needs.CATALOG
            ],
            "evidence_ladder": [
                {"key": e.value, "label": e.label, "definition": e.definition}
                for e in Evidence
            ],
            "confidence_levels": [
                {"key": c.value, "label": c.label, "definition": c.definition}
                for c in Confidence
            ],
            "thresholds": {
                "need_severity": domain_needs.NEED_SEVERITY_THRESHOLD,
                "strength_percentile": domain_needs.STRENGTH_PERCENTILE_THRESHOLD,
                "headline_rows": domain_needs.HEADLINE_ROWS,
            },
        }

    # ------------------------------------------------------- player intelligence (R9)

    def player_intelligence(self, player_id: str) -> dict[str, Any]:
        """One player's measured capabilities, with every dimension accounted for."""
        player = self.db.get(Player, player_id)
        if player is None:
            raise NotFoundError(f"player {player_id} not found")

        vectors = self._eval._skills()
        vector = vectors.get(player_id) or {}
        card = self._eval._card(player, None)

        skills = [
            self._skill_entry(dim, vector.get(dim.key))
            for dim in domain_skills.DECLARED_DIMENSIONS
        ]
        measured = [s for s in skills if s["available"]]

        row = self.db.scalars(
            select(PlayerArchetype).where(
                PlayerArchetype.player_id == player_id,
                PlayerArchetype.season == self._eval.settings.current_season,
            )
        ).first()
        memberships = domain_archetypes.single_membership(row.label if row else "")

        return {
            "player": {
                "id": player.id,
                "full_name": player.full_name,
                "position": player.position,
                "height_inches": player.height_inches,
            },
            "season": self._eval.settings.current_season,
            "skills": skills,
            "skills_measured": len(measured),
            "skills_declared": len(skills),
            "archetypes": [m.as_dict() for m in memberships],
            "impact": self._impact_entry(card),
            # Said once, plainly, rather than left for a reader to infer from gaps.
            "coverage_note": (
                f"Pivot measures {len(measured)} of {len(skills)} declared basketball "
                "dimensions from the data it ingests. The rest are listed with the reason "
                "they are unavailable rather than omitted."
            ),
        }

    @staticmethod
    def _skill_entry(dim: domain_skills.SkillDimension, value: float | None) -> dict[str, Any]:
        """One dimension, whether or not it has a value.

        A declared-but-unmeasured dimension and a measured dimension a particular player
        is missing are different absences, and both are reported as absences with their
        own reason rather than as a zero or a median.
        """
        base = {
            "key": dim.key,
            "label": dim.label,
            "side": dim.side.value,
            "definition": dim.definition,
        }
        m: Measurement[float]
        if not dim.available:
            m = Measurement.unavailable(dim.unavailable_reason)
        elif value is None:
            m = Measurement.unavailable(
                "this player's inputs for this dimension are missing, so it is not "
                "computed — a skill is omitted rather than filled with a league median"
            )
        else:
            m = Measurement.derived(
                round(float(value), 4),
                method=dim.method,
                source=SKILL_SOURCE,
                confidence=dim.confidence,
                limitations=dim.limitations,
            )
        return {**base, **m.as_dict(), "percentile": m.value}

    @staticmethod
    def _impact_entry(card: PlayerCard) -> dict[str, Any]:
        """TEI, with its band, or an explicit absence.

        `tei = 0.0` is the 63rd percentile of rostered players, so a default here would be
        a silent promotion. A player with no estimate reports none.
        """
        if card.tei is None:
            return Measurement.unavailable(
                "no impact estimate for this player — he is left out of any projection "
                "rather than given a league-average stand-in, and still counts against "
                "roster limits"
            ).as_dict()
        m = Measurement.derived(
            round(card.tei, 4),
            method=(
                "Pivot Estimated Impact (TEI): a transparent weighted z-score index over "
                "recency-weighted three-season features. A unitless index, not points per "
                "100 possessions."
            ),
            source="player_impact_estimates, from the registered player_impact model",
            confidence=Confidence.VALIDATED,
            limitations=(
                "Box-score derived. It cannot see on-ball defence, screen navigation or "
                "off-ball gravity.",
            ),
        )
        out = m.as_dict()
        out["sigma"] = card.tei_sigma
        out["availability"] = card.availability
        out["minutes"] = card.minutes
        return out

    # --------------------------------------------------------- roster profile (R11)

    def team_profile(self, team_id: str) -> dict[str, Any]:
        """What a roster contains, what it is good at, and what it lacks."""
        team = self.db.get(Team, team_id)
        if team is None:
            raise NotFoundError(f"team {team_id} not found")

        roster = self._eval._roster_cards(team_id)
        season = self._eval.settings.current_season

        rows = self.db.scalars(
            select(TeamNeed).where(TeamNeed.team_id == team_id, TeamNeed.season == season)
        ).all()
        need_rows = [
            {
                "key": r.need_key,
                "label": (d.label if (d := domain_needs.describe(r.need_key)) else r.need_key),
                "severity": r.severity,
                "percentile": r.percentile,
                "explanation": r.explanation,
                "addressed_by": domain_needs.NEED_TO_SKILL.get(r.need_key),
                "unaddressable_reason": domain_needs.UNADDRESSABLE_NEEDS.get(r.need_key, ""),
            }
            for r in rows
        ]

        weaknesses, strengths = classify_needs(need_rows)

        return {
            "team": {
                "id": team.id,
                "abbreviation": team.abbreviation,
                "full_name": team.full_name,
            },
            "season": season,
            "roster_size": len(roster),
            "skill_coverage": self._skill_coverage(roster),
            "needs": need_rows,
            "weaknesses": weaknesses,
            "strengths": strengths,
            "archetype_distribution": self._archetype_distribution(team_id, roster),
            "players_without_impact_estimate": [
                {"id": c.player_id, "name": c.name} for c in roster if not c.is_modeled
            ],
            "needs_available": bool(need_rows),
            "needs_unavailable_reason": (
                "" if need_rows else "team needs have not been computed; run `make score`"
            ),
            "classification_note": (
                f"A need is a headline weakness at severity >= "
                f"{domain_needs.NEED_SEVERITY_THRESHOLD} and a strength only at severity 0 "
                f"with percentile >= {domain_needs.STRENGTH_PERCENTILE_THRESHOLD:.0f}. The "
                "two lists cannot overlap."
            ),
        }

    def _skill_coverage(self, roster: list[PlayerCard]) -> list[dict[str, Any]]:
        """Where the roster's rotation stands in each measured skill.

        Reported as the **third-best** rotation value, which is the same statistic
        `_fit` uses to decide a roster is already strong somewhere — so the profile a user
        reads and the redundancy the evaluator charges are the same number, not two
        thresholds that can disagree.
        """
        from app.analytics.projection import ROTATION_DEPTH

        with_minutes = [c for c in roster if c.minutes is not None]
        top = sorted(with_minutes, key=lambda c: c.minutes or 0.0, reverse=True)[:ROTATION_DEPTH]

        out: list[dict[str, Any]] = []
        for key in domain_skills.SKILL_KEYS:
            dim = domain_skills.BY_KEY[key]
            values = sorted(c.skills[key] for c in top if c.skills and key in c.skills)
            if len(values) >= 3:
                m = Measurement.derived(
                    round(values[-3], 4),
                    method=(
                        f"third-best value among the top {ROTATION_DEPTH} rotation players "
                        "by minutes"
                    ),
                    source=SKILL_SOURCE,
                    confidence=dim.confidence,
                    limitations=dim.limitations,
                )
            else:
                m = Measurement.unavailable(
                    "fewer than three rotation players have this skill measured, so the "
                    "roster's strength in it is unknown — not average"
                )
            out.append(
                {
                    "key": key,
                    "label": dim.label,
                    "side": dim.side.value,
                    **m.as_dict(),
                    "rotation_players_measured": len(values),
                }
            )
        return out

    def _archetype_distribution(
        self, team_id: str, roster: list[PlayerCard]
    ) -> list[dict[str, Any]]:
        """Which functional archetypes the roster holds, and how many of each."""
        labels = self._eval._roles()
        counts: dict[str, int] = {}
        for card in roster:
            label = labels.get(card.player_id)
            if label:
                counts[label] = counts.get(label, 0) + 1
        return [
            {
                "key": key,
                "label": (d.label if (d := domain_archetypes.describe(key)) else key),
                "family": (d.family.value if (d := domain_archetypes.describe(key)) else None),
                "count": count,
            }
            for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    # ------------------------------------------------------ team-conditional fit (R12)

    def player_team_fit(self, player_id: str, team_id: str) -> dict[str, Any]:
        """How this player would fit THIS roster. There is no team-free version.

        Modelled as an addition: the player arrives and nobody departs, so his minutes come
        out of the incumbents and are priced against the roster's own minutes-weighted
        skill profile — the measured one-way baseline R5.5 established, not a fabricated
        median player.

        The score is the shipped `fit` component on its documented 0..100 scale where
        **50 means "changes nothing"**, and it carries the same disclosures the trade
        evaluator shows: which needs were addressed, which skills were redundant, which
        could not be compared, and which measured needs no player skill claims to fix.

        **Withheld when the roster has no measured need.** `fit_score` is a needs term
        minus a redundancy term. When no need clears `NEED_SEVERITY_THRESHOLD` the needs
        term is close to zero for every candidate, the score becomes a pure redundancy
        penalty, and it then ranks *better* players lower — because a better player is
        strong in more skills and is charged for each one the roster already has.

        Measured on the 30 ingested rosters. Where the need vector has signal the score
        discriminates correctly:

            DEN (POA defense 1.00, rim protection 0.86)  n=127  median 63.9  sd 28.9
                best  Jalen Smith, Moussa Cisse, Charles Bassey   (rim protection)
                worst Payton Pritchard, Collin Sexton             (small guards)
            SAC (3P volume 1.00, efficiency 0.93)        n=129  median 62.3  sd 34.8
                best  Porzingis, Jokic, Tatum                     (shooting bigs)
            MEM (defensive rebounding 1.00)              n=129  median 49.7  sd 33.7
                best  Jokic, Gafford, Porzingis                   (rebounding bigs)

        Where it does not, it inverts:

            ATL (max severity 0.172, nothing above threshold)  n=79  median 41.6
                88.6 % below neutral; worst-ranked player is Donovan Mitchell

        Two of thirty rosters are in that state today. They get an explicit unavailable
        with the reason, rather than a number that would rank a star last.
        """
        player = self.db.get(Player, player_id)
        if player is None:
            raise NotFoundError(f"player {player_id} not found")
        team = self.db.get(Team, team_id)
        if team is None:
            raise NotFoundError(f"team {team_id} not found")

        roster = self._eval._roster_cards(team_id)
        card = self._eval._card(player, None)

        score: float | None
        detail: dict[str, Any]
        if card.player_id in {c.player_id for c in roster}:
            already = True
            # Pricing a player against a roster he is already on would compare him with
            # himself. The honest answer is his current roster context, not a fit score.
            score, detail = None, {
                "unavailable": (
                    f"{player.full_name} is already on {team.abbreviation}'s roster, so "
                    "there is no addition to price. Roster fit answers what a player would "
                    "change, and he is already part of what is there."
                )
            }
        else:
            already = False
            team_needs = self._eval._team_needs(team_id)
            strongest = max(team_needs.values(), default=0.0)
            if strongest < domain_needs.NEED_SEVERITY_THRESHOLD:
                score, detail = None, {
                    "unavailable": (
                        f"{team.abbreviation}'s strongest measured need is "
                        f"{strongest:.2f}, below the {domain_needs.NEED_SEVERITY_THRESHOLD} "
                        "severity threshold. With no need to address, roster fit reduces to "
                        "a redundancy penalty and ranks better players lower rather than "
                        "higher, so no fit score is offered for this roster."
                    ),
                    "strongest_need_severity": round(strongest, 3),
                    "needs": team_needs,
                }
            else:
                score, detail = self._eval._fit(team_id, roster, incoming=[card], outgoing=[])

        return {
            "player": {"id": player.id, "full_name": player.full_name},
            "team": {
                "id": team.id,
                "abbreviation": team.abbreviation,
                "full_name": team.full_name,
            },
            "season": self._eval.settings.current_season,
            "already_on_roster": already,
            "available": score is not None,
            "score": None if score is None else round(score, 2),
            "scale_note": (
                "0-100, where 50 is the score an addition that changes nothing on this "
                "axis receives."
            ),
            "detail": detail,
            "conditional_note": (
                "Fit is conditional on this roster. The same player scores differently "
                "against a team with different needs, and Pivot does not publish a "
                "team-free fit score."
            ),
        }


# ------------------------------------------------------------------- pure classification


def classify_needs(
    need_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split need rows into headline weaknesses and strengths. Disjoint by construction.

    Pure, so the rule is testable without a database — and server-side, so every client
    gets the same answer. A weakness needs real severity; a strength needs severity
    *exactly* zero and a percentile above the bar. Nothing can satisfy both, which is the
    defect this shape exists to prevent: Atlanta once appeared under Strengths and Needs
    simultaneously for defensive rebounding, with a zero-length bar under a caption
    promising that a longer bar meant a larger shortfall (QA-9).

    The old fallback — "if nothing clears the threshold, show the top four by severity
    anyway" — is deliberately not reproduced. 135 of 279 stored rows have severity 0, so it
    presented teams with a weakness list they did not have.
    """
    weaknesses = [
        r
        for r in sorted(need_rows, key=lambda r: -(r.get("severity") or 0.0))
        if (r.get("severity") or 0.0) >= domain_needs.NEED_SEVERITY_THRESHOLD
    ][: domain_needs.HEADLINE_ROWS]

    strengths = [
        r
        for r in sorted(need_rows, key=lambda r: -(r.get("percentile") or 0.0))
        if (r.get("severity") or 0.0) == 0.0
        and r.get("percentile") is not None
        and r["percentile"] >= domain_needs.STRENGTH_PERCENTILE_THRESHOLD
    ][: domain_needs.HEADLINE_ROWS]

    return weaknesses, strengths


__all__ = ["IntelligenceService", "classify_needs"]
