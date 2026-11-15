"""The intelligence read surface.

These endpoints are new, but almost nothing they return is. The properties worth pinning
are therefore not "does the arithmetic work" — the underlying machinery has its own tests —
but the four claims this layer is responsible for:

1. A declared dimension Pivot cannot measure is **named with its reason**, not omitted.
2. A player with no impact estimate reports **no number**, not a zero.
3. Strength and weakness are decided **on the server**, from one threshold pair, and the
   two lists cannot overlap.
4. Fit is **conditional on a team**, and is **withheld** where the need vector cannot
   support it rather than served as a redundancy penalty that ranks stars last.
"""

import pytest

from app.domain import needs as domain_needs
from app.domain import skills as domain_skills
from app.services.intelligence import IntelligenceService, classify_needs


@pytest.fixture()
def svc(db, seeded_league):
    return IntelligenceService(db)


# The keys `Measurement.as_dict()` always serializes, whether or not there is a value.
MEASUREMENT_KEYS = {
    "value",
    "available",
    "evidence",
    "confidence",
    "method",
    "source",
    "limitations",
    "reason",
}


# ------------------------------------------------------------------------ vocabulary


class TestVocabulary:
    def test_it_declares_more_dimensions_than_it_measures(self):
        """The gap is the point: Pivot names what it cannot see."""
        v = IntelligenceService.vocabulary()
        available = [s for s in v["skills"] if s["available"]]
        assert len(available) == len(domain_skills.SKILL_KEYS)
        assert len(v["skills"]) > len(available)

    def test_every_unavailable_dimension_carries_a_reason(self):
        v = IntelligenceService.vocabulary()
        for s in v["skills"]:
            if not s["available"]:
                assert s["unavailable_reason"], f"{s['key']} is unavailable with no reason"

    def test_it_publishes_the_thresholds_it_classifies_by(self):
        """A client that wants to explain the classification can read the rule."""
        t = IntelligenceService.vocabulary()["thresholds"]
        assert t["need_severity"] == domain_needs.NEED_SEVERITY_THRESHOLD
        assert t["strength_percentile"] == domain_needs.STRENGTH_PERCENTILE_THRESHOLD

    def test_the_evidence_ladder_is_published_with_definitions(self):
        v = IntelligenceService.vocabulary()
        assert {e["key"] for e in v["evidence_ladder"]} == {"observed", "derived", "inferred"}
        assert all(e["definition"] for e in v["evidence_ladder"])


# ---------------------------------------------------------------- player intelligence


class TestPlayerIntelligence:
    def test_every_declared_dimension_is_accounted_for(self, svc, seeded_league):
        out = svc.player_intelligence(seeded_league["roster_a"][0].id)
        assert len(out["skills"]) == len(domain_skills.DECLARED_DIMENSIONS)
        assert out["skills_declared"] > out["skills_measured"]

    def test_an_unmeasurable_dimension_is_named_not_omitted(self, svc, seeded_league):
        out = svc.player_intelligence(seeded_league["roster_a"][0].id)
        by_key = {s["key"]: s for s in out["skills"]}
        poa = by_key["point_of_attack_defense"]
        assert poa["available"] is False
        assert poa["value"] is None
        assert poa["reason"]

    def test_a_measured_skill_carries_its_evidence_and_method(self, svc, seeded_league):
        out = svc.player_intelligence(seeded_league["roster_a"][0].id)
        measured = [s for s in out["skills"] if s["available"]]
        assert measured, "the fixture should produce at least one measured skill"
        for s in measured:
            assert s["evidence"] in {"observed", "derived", "inferred"}
            assert s["method"]
            assert s["source"]

    def test_a_player_with_no_impact_estimate_reports_no_number(self, svc, seeded_league):
        """`tei = 0.0` is the 63rd percentile of rostered players. A default here would be
        a silent promotion."""
        out = svc.player_intelligence(seeded_league["unmodeled"].id)
        assert out["impact"]["available"] is False
        assert out["impact"]["value"] is None
        assert out["impact"]["reason"]
        # An absence keeps its own classification and the bare Measurement shape: no band,
        # availability or minutes are invented for it.
        assert out["impact"]["confidence"] == "unavailable"
        assert out["impact"]["evidence"] is None
        assert set(out["impact"]) == MEASUREMENT_KEYS

    def test_a_modelled_player_reports_a_number_with_its_band(self, svc, seeded_league):
        out = svc.player_intelligence(seeded_league["roster_a"][0].id)
        assert out["impact"]["available"] is True
        assert out["impact"]["value"] is not None
        assert "sigma" in out["impact"]

    def test_the_impact_index_is_served_as_a_heuristic_not_a_validated_claim(
        self, svc, seeded_league
    ):
        """TEI is a hand-weighted composite with no fitted parameters (docs/model-card-player-
        impact.md), so the evidence ladder files it as a transparent rule — `heuristic`. Only
        the classification is asserted new; the fixture's value, band, availability and minutes
        must come through exactly as before."""
        import json

        out = svc.player_intelligence(seeded_league["roster_a"][0].id)
        impact = json.loads(json.dumps(out["impact"]))  # the shape a client receives

        assert impact["confidence"] == "heuristic"
        assert impact["evidence"] == "derived"
        assert impact["available"] is True
        assert impact["reason"] == ""
        # The fixture's first rostered player: tei -3.0, availability 0.55, 8.0 minutes, and the
        # production-width band converted to a sigma.
        assert impact["value"] == -3.0
        assert impact["sigma"] == pytest.approx(2.4620966798018027, abs=1e-12)
        assert impact["availability"] == 0.55
        assert impact["minutes"] == 8.0
        assert impact["source"] == (
            "player_impact_estimates, from the registered player_impact model"
        )
        assert impact["method"].startswith("Pivot Estimated Impact (TEI)")
        assert impact["limitations"] == [
            "Box-score derived. It cannot see on-ball defence, screen navigation or "
            "off-ball gravity."
        ]
        assert set(impact) == MEASUREMENT_KEYS | {"sigma", "availability", "minutes"}

    def test_an_unknown_player_is_a_not_found(self, svc):
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            svc.player_intelligence("no-such-player")


# --------------------------------------------------------------------- team profile


class TestTeamProfile:
    def test_it_reports_the_roster_and_its_holes(self, svc, seeded_league):
        out = svc.team_profile(seeded_league["team_a"].id)
        assert out["roster_size"] == 15
        unmodelled = {p["id"] for p in out["players_without_impact_estimate"]}
        assert seeded_league["unmodeled"].id in unmodelled

    def test_skill_coverage_covers_every_measured_dimension(self, svc, seeded_league):
        out = svc.team_profile(seeded_league["team_a"].id)
        assert [c["key"] for c in out["skill_coverage"]] == domain_skills.SKILL_KEYS

    def test_an_unmeasurable_coverage_entry_says_unknown_not_average(self, svc, seeded_league):
        """Fewer than three rotation players with a skill means the roster's strength in it
        is unknown — never 0.5."""
        out = svc.team_profile(seeded_league["team_a"].id)
        for entry in out["skill_coverage"]:
            if not entry["available"]:
                assert entry["value"] is None
                assert entry["reason"]

    def test_an_unknown_team_is_a_not_found(self, svc):
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            svc.team_profile("no-such-team")


# ------------------------------------------------------------------- classification


class TestNeedClassification:
    """The rule that used to live in the browser, now testable."""

    def test_strengths_and_weaknesses_are_disjoint(self):
        """QA-9: Atlanta showed 'Defensive rebounding 67th' under Strengths *and* Needs.

        A weakness needs real severity; a strength needs severity exactly zero. Nothing can
        satisfy both, by construction rather than by tuning.
        """
        rows = [
            {"key": f"n{i}", "severity": s, "percentile": p}
            for i, (s, p) in enumerate(
                [(0.9, 5.0), (0.5, 20.0), (0.0, 90.0), (0.0, 70.0), (0.0, 40.0), (0.2, 55.0)]
            )
        ]
        weak, strong = classify_needs(rows)
        assert {r["key"] for r in weak}.isdisjoint({r["key"] for r in strong})

    def test_a_row_below_the_severity_threshold_is_not_a_weakness(self):
        weak, _ = classify_needs([{"key": "n", "severity": 0.2, "percentile": 40.0}])
        assert weak == []

    def test_a_zero_severity_row_below_the_percentile_bar_is_not_a_strength(self):
        _, strong = classify_needs([{"key": "n", "severity": 0.0, "percentile": 50.0}])
        assert strong == []

    def test_no_fallback_invents_a_weakness_list(self):
        """The old browser rule showed the top four by severity whenever nothing cleared
        the threshold. 135 of 279 stored rows have severity 0, so it presented teams with
        a weakness list they did not have."""
        rows = [{"key": f"n{i}", "severity": 0.0, "percentile": 50.0} for i in range(6)]
        weak, _ = classify_needs(rows)
        assert weak == []

    def test_a_row_with_no_percentile_can_never_be_a_strength(self):
        _, strong = classify_needs([{"key": "n", "severity": 0.0, "percentile": None}])
        assert strong == []

    def test_both_lists_are_capped(self):
        rows = [{"key": f"w{i}", "severity": 0.9, "percentile": 5.0} for i in range(10)]
        weak, _ = classify_needs(rows)
        assert len(weak) == domain_needs.HEADLINE_ROWS

    def test_weaknesses_are_ordered_by_severity(self):
        rows = [
            {"key": "low", "severity": 0.4, "percentile": 30.0},
            {"key": "high", "severity": 0.95, "percentile": 2.0},
        ]
        weak, _ = classify_needs(rows)
        assert [r["key"] for r in weak] == ["high", "low"]


# ------------------------------------------------------------- team-conditional fit


class TestFitIsConditional:
    def test_there_is_no_team_free_fit_entry_point(self):
        """The signature is the claim: no universal player fit score exists."""
        import inspect

        params = inspect.signature(IntelligenceService.player_team_fit).parameters
        assert "team_id" in params
        assert params["team_id"].default is inspect.Parameter.empty

    def test_a_player_already_on_the_roster_gets_no_score(self, svc, seeded_league):
        """Pricing him against his own roster would compare him with himself."""
        out = svc.player_team_fit(
            seeded_league["roster_a"][0].id, seeded_league["team_a"].id
        )
        assert out["already_on_roster"] is True
        assert out["score"] is None
        assert out["available"] is False

    def test_a_roster_with_no_measured_need_gets_no_score(self, svc, seeded_league, db):
        """With no need to address, fit reduces to a redundancy penalty and ranks better
        players lower. Measured on ATL: 88.6 % below neutral, worst-ranked player Donovan
        Mitchell. A number would be worse than an absence."""
        from app.db.models import TeamNeed

        team_a = seeded_league["team_a"]
        for row in db.query(TeamNeed).filter(TeamNeed.team_id == team_a.id).all():
            row.severity = 0.1
        db.flush()
        svc._eval._needs_cache.clear()

        out = svc.player_team_fit(seeded_league["roster_b"][0].id, team_a.id)
        assert out["available"] is False
        assert out["score"] is None
        assert "below the" in out["detail"]["unavailable"]

    def test_a_scored_fit_states_its_scale_and_its_conditionality(self, svc, seeded_league):
        out = svc.player_team_fit(
            seeded_league["roster_b"][0].id, seeded_league["team_a"].id
        )
        assert "50" in out["scale_note"]
        assert "conditional" in out["conditional_note"].lower()

    def test_an_unknown_player_or_team_is_a_not_found(self, svc, seeded_league):
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            svc.player_team_fit("nope", seeded_league["team_a"].id)
        with pytest.raises(NotFoundError):
            svc.player_team_fit(seeded_league["roster_a"][0].id, "nope")
