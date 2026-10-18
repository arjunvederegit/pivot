"""The evidence grid: documented observations per priority, never a score."""

from __future__ import annotations

from app.assistant_rag.corpus import Chunk
from app.assistant_rag.grid import (
    GRID_LABEL,
    MAX_DIMENSIONS,
    MAX_ROWS,
    NO_OBSERVATION,
    build_evidence_grid,
    grid_dimensions,
    latest_assessment,
    stat_line,
)
from app.assistant_rag.manifest import load_manifest
from app.assistant_rag.types import (
    EntityRef,
    EntityResolution,
    QueryContext,
    RetrievalResult,
    RetrievedChunk,
)

M = load_manifest()


def ctx(*, mode="roster_fit", team_id="COMETS", priorities=None):
    return QueryContext(
        question="q",
        mode=mode,
        team_id=team_id,
        priorities=dict(priorities or {}),
        previous_priorities=None,
        constraints=(),
        history=(),
        history_received=0,
        history_truncated=False,
        prompt_variant="grounded",
        retrieval_variant="hybrid",
        k=6,
    )


def resolve(*player_ids, how="named", team_id="COMETS"):
    refs = [EntityRef(pid, M.player(pid).name, "player", how) for pid in player_ids]
    if team_id:
        refs.append(EntityRef(team_id, M.team(team_id).name, "team", "selected_team"))
    return EntityResolution(tuple(refs), (), ())


def retrieval(*sids):
    items = []
    for i, sid in enumerate(sids):
        meta = M.document(sid)
        c = Chunk(f"{sid}@{meta.version}#1", sid, meta.version, 1, None, "text", "0" * 64, meta)
        items.append(RetrievedChunk(c, i + 1, 0.1, 0.6, i + 1, 1.0, i + 1, meta.entity_ids, False, ()))
    return RetrievalResult("q", "hybrid", 6, tuple(items), tuple(items), 0.7, 0.45, False)


def cell(grid, player_id, dimension):
    row = next(r for r in grid.rows if r.player_id == player_id)
    return next(c for c in row.cells if c.dimension == dimension)


class TestWhenTheGridAppears:
    def test_research_mode_has_no_grid(self):
        assert build_evidence_grid(ctx(mode="research"), resolve("NIKO_REED"), retrieval("S03"), M) is None

    def test_no_priorities_and_no_team_has_no_grid(self):
        assert build_evidence_grid(ctx(team_id=None), resolve("NIKO_REED", team_id=None), retrieval("S03"), M) is None

    def test_no_eligible_players_has_no_grid(self):
        # Sorensen and Valdez are already Comets.
        grid = build_evidence_grid(ctx(), resolve(), retrieval("S16", "S18"), M)
        assert grid is None


class TestRowsAndColumns:
    def test_label_and_note_say_it_is_not_a_score(self):
        grid = build_evidence_grid(ctx(), resolve("NIKO_REED", "THEO_VANCE"), retrieval("S03"), M)
        assert grid.label == GRID_LABEL == "Documented observations — not a fit score"
        for phrase in ("Non-official", "unranked", "Nothing is weighted, summed or ranked", "Hartford Comets"):
            assert phrase in grid.note

    def test_named_players_are_rows_in_order(self):
        grid = build_evidence_grid(ctx(), resolve("THEO_VANCE", "NIKO_REED"), retrieval("S03", "S05"), M)
        assert [r.player_id for r in grid.rows] == ["THEO_VANCE", "NIKO_REED"]
        assert grid.rows[0].current_club == "Bayou Herons"

    def test_carried_players_count_as_named(self):
        grid = build_evidence_grid(ctx(), resolve("BASTIAN_HOLLOWAY", how="carried_from_history"), retrieval("S03"), M)
        assert [r.player_id for r in grid.rows] == ["BASTIAN_HOLLOWAY"]

    def test_falls_back_to_retrieved_players_excluding_own_team(self):
        grid = build_evidence_grid(ctx(), resolve(), retrieval("S16", "S03", "S02"), M)
        ids = [r.player_id for r in grid.rows]
        assert ids[0] == "NIKO_REED"  # S16 (Sorensen) is a Comet, skipped
        assert "RAFAEL_SORENSEN" not in ids and "NIKO_VALDEZ" not in ids
        assert ids[1:] == ["MATEO_CRUZ", "BASTIAN_HOLLOWAY", "ILAN_MARCHETTI", "DORIAN_PIKE"][: MAX_ROWS - 1]
        assert len(ids) <= MAX_ROWS

    def test_named_players_on_own_team_are_excluded(self):
        grid = build_evidence_grid(ctx(), resolve("RAFAEL_SORENSEN", "THEO_VANCE"), retrieval("S16"), M)
        assert [r.player_id for r in grid.rows] == ["THEO_VANCE"]

    def test_dimensions_follow_levels_then_manifest_order(self):
        # Comets defaults: POA high, wing high, spacing must_preserve, secondary medium, rebounding low.
        grid = build_evidence_grid(ctx(), resolve("NIKO_REED"), retrieval("S03"), M)
        assert grid.dimensions == ["spacing", "point_of_attack_defense", "wing_defense", "secondary_creation"]
        assert "the team's default priorities" in grid.note

    def test_gm_priorities_override_defaults_and_cap_at_five(self):
        priorities = {
            "availability": "medium",
            "rebounding": "high",
            "size": "high",
            "spacing": "must_preserve",
            "youth_upside": "medium",
            "rim_protection": "high",
            "team_defense": "low",
        }
        dims = grid_dimensions(priorities, M)
        assert dims == ["spacing", "rim_protection", "rebounding", "size", "youth_upside"]
        assert len(dims) == MAX_DIMENSIONS
        grid = build_evidence_grid(ctx(priorities=priorities), resolve("DANTE_CRUZ"), retrieval("S10"), M)
        assert grid.dimensions == dims
        assert "the GM's priorities" in grid.note


class TestCells:
    def test_most_recent_assessment_wins(self):
        assert latest_assessment("WES_CALDER", "wing_defense", M).source_id == "S12"  # 2026 over 2023
        assert latest_assessment("THEO_VANCE", "point_of_attack_defense", M).grade == "B"  # Jan 2026 over Nov 2025
        assert latest_assessment("NIKO_REED", "rim_protection", M) is None

    def test_cell_carries_grade_source_date_and_conflict(self):
        grid = build_evidence_grid(ctx(), resolve("THEO_VANCE"), retrieval("S05"), M)
        poa = cell(grid, "THEO_VANCE", "point_of_attack_defense")
        assert (poa.grade, poa.source_id, poa.report_date, poa.conflict_id) == ("B", "S05", "2026-01-28", "C1")
        spacing = cell(grid, "THEO_VANCE", "spacing")
        assert spacing.stat_line == "3P 40.4% on 6.1 3PA"
        assert spacing.conflict_id is None

    def test_missing_observation_is_explicit_not_neutral(self):
        grid = build_evidence_grid(ctx(), resolve("DANTE_CRUZ"), retrieval("S10"), M)
        poa = cell(grid, "DANTE_CRUZ", "point_of_attack_defense")
        assert poa.grade is None and poa.source_id is None and poa.report_date is None
        assert poa.summary == NO_OBSERVATION

    def test_stat_lines(self):
        assert stat_line("DANTE_CRUZ", "rebounding", M) == "10.9 RPG"
        assert stat_line("DANTE_CRUZ", "rim_protection", M) == "2.3 BPG"
        assert stat_line("WES_CALDER", "availability", M) == "63 GP"
        assert stat_line("MATEO_CRUZ", "youth_upside", M) == "Age 23"
        assert "withholds a percentage" in stat_line("DANTE_CRUZ", "spacing", M)
        assert stat_line("NIKO_REED", "point_of_attack_defense", M) is None

    def test_grid_values_are_manifest_values_not_scores(self):
        grid = build_evidence_grid(ctx(), resolve("NIKO_REED", "THEO_VANCE"), retrieval("S03"), M)
        grades = {a.grade for a in M.assessments} | {None}
        for row in grid.rows:
            for c in row.cells:
                assert c.grade in grades
