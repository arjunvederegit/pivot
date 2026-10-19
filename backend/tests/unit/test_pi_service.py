"""R9.2 — ``PlayerIntelligenceService``: one retrieval path, honest refusals, candidates labelled as such.

Runs on a synthetic league whose shooting statistics are generated from known latent abilities and integer
counts, rounded the way NBA.com rounds them (``pi_support``)."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CandidateEstimate, ModelVersion, PlayerSeasonStats
from app.modeling.contract import EvaluationStatus, OutputKind
from app.modeling.registry import protected_fingerprint, reject_candidate
from app.player_intelligence.service import (
    InvalidRequestError,
    PlayerIntelligenceService,
    serving_decision,
)
from app.player_intelligence.validation import ValidationRegistry

from .pi_support import RUN_ID as RUN_ID_SUPPORT
from .pi_support import build_league, make_registry

CUTOFF = "2023-24"
PROJECTED_FOR = "2024-25"
RUN_ID = RUN_ID_SUPPORT


def registry(**overrides) -> ValidationRegistry:
    return make_registry(FINGERPRINT["value"], **overrides)


FINGERPRINT: dict[str, str] = {}


@pytest.fixture(scope="module")
def league(tmp_path_factory):
    """Built once: the synthetic league and two stored candidates at the 2023-24 cutoff, trained against a fake
    experiment so the registry's evidence legitimately applies to them."""
    info = build_league(tmp_path_factory, seed=4, cutoff=CUTOFF)
    FINGERPRINT["value"] = info["fingerprint"]
    yield info
    info["db"].close()
    info["engine"].dispose()


@pytest.fixture()
def svc(league):
    return PlayerIntelligenceService(league["db"], registry())


def pid(league, rank: int = 0) -> str:
    """A player with a full record at the cutoff (the most-minutes players are in every season)."""
    frame = league["frame"]
    at = frame[frame["season"] == CUTOFF].sort_values("total_minutes", ascending=False)
    return league["players"][at.iloc[rank]["player_id"]].id


class TestMeasured:
    def test_measured_shooting_is_made_over_attempted_with_its_sample(self, svc, league):
        player = pid(league)
        r = svc.get(player, ["fg3_pct", "ft_pct", "ts_pct"], as_of=CUTOFF)
        by = {x.quantity: x for x in r.records}
        assert set(by) == {"fg3_pct", "ft_pct", "ts_pct"}
        src = next(k for k, v in league["players"].items() if v.id == player)
        t = league["truth"][(src, CUTOFF)]
        assert by["fg3_pct"].value == pytest.approx(t["fg3m"] / t["fg3a"], abs=0.02)
        assert by["fg3_pct"].sample.attempts == pytest.approx(t["fg3a"], abs=4)
        assert by["fg3_pct"].sample.games == t["gp"]
        assert by["fg3_pct"].output_kind is OutputKind.MEASURED and by["fg3_pct"].model is None
        assert by["fg3_pct"].period.season == CUTOFF and by["fg3_pct"].provenance.sources[0].provider == "nba_api"
        assert by["fg3_pct"].provenance.data_snapshot_id.startswith("ds-")

    def test_no_attempts_is_unavailable_not_zero(self, svc, league):
        player = pid(league, 5)
        rows = league["db"].scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.player_id == player, PlayerSeasonStats.season == CUTOFF)).all()
        saved = {}
        try:
            for row in rows:
                saved[row.id] = dict(row.stats)
                if row.stat_type == "base":
                    row.stats = {**row.stats, "FG3M": 0.0, "FG3A": 0.0, "FG3_PCT": 0.0}
                else:
                    row.stats = {**row.stats, "EFG_PCT": row.stats["EFG_PCT"]}
            league["db"].flush()
            r = svc.get(player, ["fg3_pct"], as_of=CUTOFF)
            assert [x.quantity for x in r.records] == []
            assert "no attempts" in r.unavailable[0].reason and "not 0%" in r.unavailable[0].reason
        finally:
            league["db"].rollback()

    def test_a_missing_season_is_reported_not_filled(self, svc, league):
        r = svc.get(pid(league), ["fg3_pct", "usg_pct"], as_of="2030-31")
        assert r.records == []
        assert all("no statistics for this player in 2030-31" in u.reason for u in r.unavailable)

    def test_as_of_never_reads_later_seasons(self, svc, league):
        player = pid(league)
        early = svc.get(player, ["fg3_pct"], as_of="2019-20")
        assert early.records[0].period.season == "2019-20"
        assert early.as_of == "2019-20"

    def test_unit_is_a_fraction_and_percentage_points_are_rejected(self, svc, league):
        player = pid(league)
        r = svc.get(player, ["usg_pct", "ast_pct", "reb_pct"], as_of=CUTOFF)
        assert all(0.0 <= x.value <= 1.0 and x.unit == "fraction of 1" for x in r.records)
        rows = league["db"].scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.player_id == player, PlayerSeasonStats.season == CUTOFF, PlayerSeasonStats.stat_type == "advanced")).all()
        try:
            rows[0].stats = {**rows[0].stats, "USG_PCT": 28.6}  # a provider that switched to percentage points
            league["db"].flush()
            bad = svc.get(player, ["usg_pct"], as_of=CUTOFF)
            assert bad.records == [] and "not a fraction of 1" in bad.unavailable[0].reason
        finally:
            league["db"].rollback()

    def test_playmaking_rebounding_role_and_availability_are_measured_only(self, svc, league):
        r = svc.get(pid(league), ["ast_pct", "ast_to_ratio", "reb_pct", "oreb_pct", "dreb_pct", "usg_pct", "minutes_per_game", "games_played"], as_of=CUTOFF)
        assert len(r.records) == 8 and all(x.output_kind is OutputKind.MEASURED for x in r.records)
        asked = svc.get(pid(league), ["ast_pct"], as_of=CUTOFF, kinds=["inferred", "projected"], include_experimental=True)
        assert asked.records == [] and len(asked.unavailable) == 2
        assert "role and teammates" in asked.unavailable[0].reason


class TestModelOutputs:
    def test_a_candidate_output_is_withheld_unless_requested_and_then_labelled(self, svc, league):
        player = pid(league)
        plain = svc.get(player, ["fg3_pct"], as_of=CUTOFF, kinds=["inferred", "projected"])
        assert plain.records == []
        assert all("include_experimental" in u.reason for u in plain.unavailable)
        r = svc.get(player, ["fg3_pct"], as_of=CUTOFF, kinds=["inferred", "projected"], include_experimental=True)
        kinds = {x.output_kind for x in r.records}
        assert kinds == {OutputKind.INFERRED, OutputKind.PROJECTED}
        for x in r.records:
            assert x.model.status == "candidate"
            assert x.model.evaluation_status is EvaluationStatus.DEVELOPMENT_ONLY
            assert x.model.version.startswith("cand-state_space-fg3_pct-")
            assert x.provenance.data_snapshot_id and x.provenance.reproducibility["seed"] == 20260719
        assert r.notices and "experimental" in r.notices[0]

    def test_a_projection_states_its_condition_and_the_participation_caveat(self, svc, league):
        r = svc.get(pid(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], include_experimental=True)
        p = r.records[0]
        assert p.target_period.season == PROJECTED_FOR and p.period.as_of_season == CUTOFF
        assert "plays at least 500 minutes" in p.conditioning and "50 three-point attempts" in p.conditioning
        assert "not a forecast of whether he will play" in p.limitations[0] and "46%" in p.limitations[0]
        assert p.interval.kind == "prediction" and p.interval.calibration.empirical_coverage == pytest.approx(0.79)
        assert 0.0 < p.value < 1.0 and p.interval.low < p.value < p.interval.high

    def test_inferred_ability_carries_a_credible_interval_and_no_borrowed_coverage(self, svc, league):
        r = svc.get(pid(league), ["fg3_pct"], as_of=CUTOFF, kinds=["inferred"], include_experimental=True)
        x = r.records[0]
        assert x.interval.kind == "credible" and x.interval.calibration is None
        assert "frequentist coverage is not claimed" in x.reliability_statement()
        assert x.reliability is not None and 0 <= x.reliability.reliability <= 1
        assert "never observed" in " ".join(x.limitations)
        assert x.target_period is None and x.conditioning is None

    def test_thin_evidence_means_lower_reliability_and_more_shrinkage(self, league):
        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%")))
        rows = db.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == v.id, CandidateEstimate.target == "fg3_pct@ability")).all()
        assert len(rows) > 50
        by_exposure = sorted(rows, key=lambda e: e.exposure)
        low, high = by_exposure[: len(rows) // 4], by_exposure[-len(rows) // 4 :]
        mean = lambda xs, k: sum(e.inputs[k] for e in xs) / len(xs)  # noqa: E731
        assert mean(low, "reliability") < mean(high, "reliability")
        assert mean(low, "weight_on_player_record") < mean(high, "weight_on_player_record")
        assert mean(low, "ability_sd") > mean(high, "ability_sd")

    def test_withheld_by_the_registry_is_never_served_even_on_request(self, league):
        svc = PlayerIntelligenceService(league["db"], registry())
        r = svc.get(pid(league), ["ts_pct"], as_of=CUTOFF, kinds=["inferred"], include_experimental=True)
        assert r.records == [] and "withheld" in r.unavailable[0].reason

    def test_a_cutoff_with_no_stored_model_is_reported_never_computed_on_the_fly(self, league):
        db = league["db"]
        before = db.query(ModelVersion).count()
        svc = PlayerIntelligenceService(db, registry())
        r = svc.get(pid(league), ["fg3_pct"], as_of="2021-22", kinds=["projected"], include_experimental=True)
        assert r.records == [] and "has not been materialised" in r.unavailable[0].reason
        assert db.query(ModelVersion).count() == before

    def test_a_failed_or_rejected_model_is_not_served(self, league):
        db = league["db"]
        failed = registry(fg3_pct={"projected": {"method": "state_space", "tier": "experimental", "evaluation_status": "failed", "evaluation_ref": "x", "reason": "r"}})
        svc = PlayerIntelligenceService(db, failed)
        r = svc.get(pid(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], include_experimental=True)
        assert r.records == [] and "failed its pre-registered evaluation" in r.unavailable[0].reason

    def test_explicit_model_version_selects_that_model(self, league):
        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-ols_history-fg3_pct-%")))
        svc = PlayerIntelligenceService(db, registry())
        r = svc.get(pid(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], model_version=v.version, include_experimental=True)
        assert r.records and r.records[0].model.version == v.version
        assert "OLS" in r.records[0].model.methodology

    def test_reading_never_writes(self, league):
        db = league["db"]
        before = protected_fingerprint(db)
        svc = PlayerIntelligenceService(db, registry())
        svc.get(pid(league), None, as_of=CUTOFF, kinds=["measured", "inferred", "projected"], include_experimental=True)
        svc.catalog()
        assert protected_fingerprint(db) == before

    def test_the_same_question_gives_the_same_answer_twice(self, league):
        svc = PlayerIntelligenceService(league["db"], registry())
        a = svc.get(pid(league), ["fg3_pct", "ts_pct"], as_of=CUTOFF, kinds=["measured", "inferred", "projected"], include_experimental=True).as_dict()
        b = svc.get(pid(league), ["fg3_pct", "ts_pct"], as_of=CUTOFF, kinds=["measured", "inferred", "projected"], include_experimental=True).as_dict()
        assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


class TestRequests:
    def test_unknown_quantity_and_kind_are_422_style_errors(self, svc, league):
        with pytest.raises(InvalidRequestError, match="unknown quantity"):
            svc.get(pid(league), ["swag_rating"])
        with pytest.raises(InvalidRequestError, match="unknown output kind"):
            svc.get(pid(league), ["fg3_pct"], kinds=["guessed"])
        with pytest.raises(InvalidRequestError, match="as_of"):
            svc.get(pid(league), ["fg3_pct"], as_of="last year")

    def test_dimensions_pivot_cannot_see_are_named_not_invented(self, svc, league):
        r = svc.get(pid(league), ["defensive_contribution", "overall_skill_rating"], as_of=CUTOFF)
        assert r.records == []
        reasons = {u.quantity: u.reason for u in r.unavailable}
        assert "blocks, steals and rebounds are recorded events" in reasons["defensive_contribution"]
        assert "deliberately not offered" in reasons["overall_skill_rating"]

    def test_an_unknown_player_is_not_found(self, svc):
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            svc.get("no-such-player")

    def test_the_catalog_lists_every_quantity_and_what_is_declined(self, svc):
        c = svc.catalog()
        q = {x["id"]: x for d in c["dimensions"].values() for x in d["quantities"]}
        assert {"ts_pct", "fg3_pct", "ft_pct", "ast_pct", "reb_pct", "usg_pct", "games_played"} <= set(q)
        assert q["usg_pct"]["kinds"]["inferred"]["tier"] == "not_offered" and "not an ability" in q["usg_pct"]["kinds"]["inferred"]["reason"]
        assert "defensive_contribution" in c["unavailable"] and "overall_skill_rating" in c["unavailable"]


class TestServingDecision:
    @pytest.mark.parametrize(
        ("status", "evaluation", "include", "allowed"),
        [
            ("candidate", EvaluationStatus.DEVELOPMENT_ONLY, False, False),
            ("candidate", EvaluationStatus.DEVELOPMENT_ONLY, True, True),
            ("candidate", EvaluationStatus.UNEVALUATED, True, True),
            ("candidate", EvaluationStatus.FAILED, True, False),
            ("rejected", EvaluationStatus.DEVELOPMENT_ONLY, True, False),
            ("retired", EvaluationStatus.HOLDOUT_CONFIRMED, True, False),
            ("active", EvaluationStatus.HOLDOUT_CONFIRMED, False, True),
            ("active", EvaluationStatus.DEVELOPMENT_ONLY, False, False),
        ],
    )
    def test_the_rule(self, status, evaluation, include, allowed):
        assert serving_decision(status, evaluation, include)[0] is allowed

    def test_a_candidate_never_reports_itself_as_validated(self):
        ok, why = serving_decision("candidate", EvaluationStatus.HOLDOUT_CONFIRMED, True)
        assert ok and why == "experimental"


def test_a_rejected_candidate_stops_being_served(league):
    db: Session = league["db"]
    from app.modeling.registry import train_candidate as tc

    tc(db, "eb_shrinkage", "fg3_pct", CUTOFF)  # a third candidate we may reject without disturbing the others
    v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-eb_shrinkage-fg3_pct-%")))
    reg = registry(fg3_pct={"projected": {"method": "eb_shrinkage", "tier": "experimental", "evaluation_status": "development_only", "evaluation_ref": "x", "reason": "r"}})
    svc = PlayerIntelligenceService(db, reg)
    assert svc.get(pid(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], include_experimental=True).records
    reject_candidate(db, v.version, "test")
    out = svc.get(pid(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], include_experimental=True)
    assert out.records == []


class TestCrossToolConsumers:
    """Two consumers with different needs read the same numbers through the same service."""

    def test_a_roster_view_and_a_player_view_cannot_disagree(self, league):
        svc = PlayerIntelligenceService(league["db"], registry())
        frame = league["frame"]
        top = frame[frame["season"] == CUTOFF].sort_values("total_minutes", ascending=False).head(6)
        ids = [league["players"][p].id for p in top["player_id"]]
        batch = svc.get_many(ids, ["fg3_pct", "usg_pct"], as_of=CUTOFF)
        assert set(batch) == set(ids)
        for pid in ids:
            single = svc.get(pid, ["fg3_pct", "usg_pct"], as_of=CUTOFF)
            assert [r.model_dump() for r in batch[pid].records] == [r.model_dump() for r in single.records]

        # consumer A (a Team Outlook-style roster aggregate) and consumer B (a Trade Evaluator-style comparison)
        def roster_three_point_rate(responses):
            made = att = 0.0
            for r in responses.values():
                for rec in r.records:
                    if rec.quantity == "fg3_pct":
                        made += rec.sample.makes
                        att += rec.sample.attempts
            return made / att

        def better_shooter(a, b):
            va = next(r for r in batch[a].records if r.quantity == "fg3_pct")
            vb = next(r for r in batch[b].records if r.quantity == "fg3_pct")
            return a if va.value >= vb.value else b

        assert 0.2 < roster_three_point_rate(batch) < 0.5
        assert better_shooter(ids[0], ids[1]) in ids[:2]
        # one provenance vocabulary for both consumers
        assert {r.provenance.sources[0].provider for resp in batch.values() for r in resp.records} == {"nba_api"}

    def test_the_batch_shares_one_cutoff_and_deduplicates(self, league):
        svc = PlayerIntelligenceService(league["db"], registry())
        ids = [league["players"][p].id for p in list(league["players"])[:3]]
        out = svc.get_many(ids + ids, ["usg_pct"], as_of=CUTOFF)
        assert len(out) == 3 and {r.as_of for r in out.values()} == {CUTOFF}


class TestPromotionReadiness:
    def test_nothing_is_eligible_and_the_reasons_are_specific(self, league):
        from app.player_intelligence.promotion import promotion_readiness

        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%")))
        r = promotion_readiness(db, v.version, registry())
        assert r.eligible is False
        text = " ".join(r.blockers)
        assert "not holdout-confirmed" in text and "no human approval record" in text and "re-run" in text

    def test_a_non_candidate_is_not_promotable(self, league):
        from app.player_intelligence.promotion import promotion_readiness

        assert promotion_readiness(league["db"], "no-such-version", registry()).eligible is False


def test_a_stale_evaluation_is_reported_as_unevaluated_not_as_the_registry_says(league):
    db: Session = league["db"]
    v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%")))
    saved = dict(v.validation_metrics or {})
    try:
        v.validation_metrics = {**saved, "evaluation_status": "stale:development_only"}
        db.flush()
        svc = PlayerIntelligenceService(db, registry())
        r = svc.get(pid(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], include_experimental=True)
        assert r.records and r.records[0].model.evaluation_status is EvaluationStatus.UNEVALUATED
    finally:
        db.rollback()


def test_an_ability_estimate_is_not_blocked_by_its_models_forecast_confirmation_status(league):
    """A model whose *forecasts* were not confirmed against the baseline can still serve an ability estimate that met
    its own rule (non-inferior forecasts, calibrated predictive interval); its projections stay blocked."""
    db: Session = league["db"]
    v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%")))
    saved = dict(v.validation_metrics or {})
    try:
        v.validation_metrics = {**saved, "evaluation_status": "holdout_not_confirmed"}
        db.flush()
        svc = PlayerIntelligenceService(db, registry())
        r = svc.get(pid(league), ["fg3_pct"], as_of=CUTOFF, kinds=["inferred", "projected"], include_experimental=True)
        assert [x.output_kind for x in r.records] == [OutputKind.INFERRED]
        assert "failed its pre-registered evaluation" in r.unavailable[0].reason
    finally:
        db.rollback()


def test_a_player_with_no_evidence_gets_no_estimate_not_the_league_curve(league):
    """A stored row built from zero attempts is the league curve; serving it as an estimate would invent a rating."""
    db: Session = league["db"]
    v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%")))
    row = db.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == v.id, CandidateEstimate.target == "fg3_pct@ability")).first()
    saved = (row.exposure, dict(row.inputs))
    try:
        row.exposure, row.inputs = 0.0, {**row.inputs, "n_observed": 0.0, "reliability": 0.0, "weight_on_player_record": 0.0}
        db.flush()
        svc = PlayerIntelligenceService(db, registry())
        r = svc.get(row.player_id, ["fg3_pct"], as_of=CUTOFF, kinds=["inferred"], include_experimental=True)
        assert r.records == [] and "nothing to infer from" in r.unavailable[0].reason
    finally:
        db.rollback()
        row.exposure, row.inputs = saved
