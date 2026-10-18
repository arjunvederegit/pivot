"""The context catalog (teams, players, dimensions, starters) and the priority diff."""

from __future__ import annotations

from app.assistant_rag import claims
from app.assistant_rag.context import (
    STARTERS,
    build_context_catalog,
    describe_change,
    priority_changes,
)
from app.assistant_rag.manifest import load_manifest
from app.assistant_rag.policy import decide
from app.assistant_rag.types import Ambiguity, EntityRef, EntityResolution, QueryContext

M = load_manifest()


def from_text(question, team_id):
    refs, ambiguous = [], []
    for mention in claims.find_mentions(question, M):
        if mention.ambiguous:
            ambiguous.append(Ambiguity(mention.text, mention.entity_ids))
        elif mention.kind == "player":
            refs.append(EntityRef(mention.entity_ids[0], M.player(mention.entity_ids[0]).name, "player", "named"))
    if team_id:
        refs.append(EntityRef(team_id, M.team(team_id).name, "team", "selected_team"))
    return EntityResolution(tuple(refs), tuple(ambiguous), ())


def ctx(question, mode, team_id):
    return QueryContext(question, mode, team_id, {}, None, (), (), 0, False, "grounded", "hybrid", 6)


class TestCatalog:
    def test_catalog_mirrors_the_manifest(self):
        catalog = build_context_catalog(M)
        assert catalog.data_mode == "synthetic_demo"
        assert catalog.label == M.label and catalog.disclaimer == M.disclaimer
        assert catalog.corpus_version == M.corpus_version
        assert catalog.league == "Pivot Synthetic League" and catalog.season == M.season
        assert catalog.modes == ["research", "roster_fit"]
        assert catalog.priority_levels == ["must_preserve", "high", "medium", "low"]
        assert [d.key for d in catalog.priority_dimensions] == [d.key for d in M.priority_dimensions]
        assert {t.id for t in catalog.teams} == {"COMETS", "RIVERHAWKS", "SUNDOGS", "TIDES"}
        assert len(catalog.players) == len(M.players)

    def test_teams_carry_defaults(self):
        comets = next(t for t in build_context_catalog(M).teams if t.id == "COMETS")
        assert comets.default_priorities["spacing"] == "must_preserve"
        assert comets.default_constraints and comets.strategy_source_ids == ["S20", "S21", "S22"]

    def test_players_show_club_names(self):
        reed = next(p for p in build_context_catalog(M).players if p.id == "NIKO_REED")
        assert reed.current_club == "Mesa Foxes" and reed.position == "SF" and reed.age == 27


class TestStarters:
    def test_eight_to_twelve_starters_across_modes_and_teams(self):
        starters = build_context_catalog(M).starters
        assert 8 <= len(starters) <= 12
        assert {s.mode for s in starters} == {"research", "roster_fit"}
        assert {s.team_id for s in starters if s.team_id} == {"COMETS", "RIVERHAWKS", "SUNDOGS", "TIDES"}
        assert all(s.intent for s in starters)

    def test_starters_only_name_fictional_entities(self):
        known = {p.name for p in M.players}
        for _, _, prompt, _ in STARTERS:
            for mention in claims.find_mentions(prompt, M):
                if mention.kind == "player":
                    assert not mention.ambiguous, prompt
                    assert M.player(mention.entity_ids[0]).name in known

    def test_starters_route_as_intended(self):
        routes = {}
        for mode, team_id, prompt, _ in STARTERS:
            decision = decide(ctx(prompt, mode, team_id), from_text(prompt, team_id), M)
            routes[prompt] = (decision.route, [u.kind for u in decision.unsupported])
        legality = [p for p in routes if "legal" in p.lower()]
        assert len(legality) == 1
        assert routes[legality[0]][0] == "refuse"
        for prompt, (route, kinds) in routes.items():
            if prompt not in legality:
                assert route == "answer" and kinds == [], prompt


class TestPriorityChanges:
    def test_no_previous_turn_means_no_changes(self):
        assert priority_changes(None, {"spacing": "high"}, M) == []

    def test_added_removed_changed_in_dimension_order(self):
        changes = priority_changes(
            {"spacing": "medium", "rebounding": "high", "size": "low"},
            {"spacing": "must_preserve", "point_of_attack_defense": "high", "size": "low"},
            M,
        )
        assert [(c.key, c.before, c.after) for c in changes] == [
            ("point_of_attack_defense", None, "high"),
            ("spacing", "medium", "must_preserve"),
            ("rebounding", "high", None),
        ]
        assert changes[0].label == "Point-of-attack defense"
        assert [describe_change(c) for c in changes] == [
            "Point-of-attack defense: added at high",
            "Spacing: medium -> must preserve",
            "Rebounding: removed (was high)",
        ]

    def test_empty_previous_means_everything_was_added(self):
        changes = priority_changes({}, {"youth_upside": "high"}, M)
        assert [(c.key, c.before, c.after) for c in changes] == [("youth_upside", None, "high")]

    def test_unknown_keys_are_ignored(self):
        assert priority_changes({"made_up": "high"}, {}, M) == []
