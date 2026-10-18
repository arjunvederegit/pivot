"""Multi-component trade evaluation.

    U = w_P*P + w_F*F + w_C*C + w_T*T + w_A*A + w_R*R      (components on 0..100)

Components with unavailable data (e.g. contract value without a contract provider)
are excluded and the remaining weights renormalized — the composite never contains an
invented number. Every evaluation stores raw calculations, uncertainty, and
sensitivity so the composite is explainable rather than a magic score."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.age_curve import timeline_alignment
from app.analytics.archetypes import (
    REPLACEMENT_SKILL_RULE,
    REPLACEMENT_SKILLS,
    SKILL_KEYS,
    UNADDRESSABLE_NEEDS,
    player_skill_vector,
    skill_schema_fingerprint,
)
from app.analytics.components import affine_score, bounded_score
from app.analytics.features import build_player_season_features, recency_weighted_features
from app.analytics.fit import fit_score
from app.analytics.needs import NEED_TO_SKILL
from app.analytics.picks import (
    REFERENCE_SLOT,
    PickTerms,
    pick_value,
    relative_pick_value,
)
from app.analytics.projection import (
    ROTATION_DEPTH,
    RotationPlayer,
    RotationResult,
    allocate_rotation,
    net_rating_delta_to_wins,
    team_tei_to_net_rating_delta,
)
from app.analytics.roster_shape import league_role_reference, role_minutes, shape_report
from app.analytics.sensitivity import (
    component_contributions,
    composite_utility,
    normalize_weights,
    tornado,
)
from app.analytics.uncertainty import (
    RotationDraw,
    rotation_draw_from,
    simulate_delta_wins,
)
from app.canonical.rosters import current_roster_clause, resolve_roster_season
from app.cba import resolver
from app.cba.builder import build_trade_context, load_cap_params, player_salaries
from app.cba.engine import TradeLegalityEngine
from app.config import get_settings
from app.core.cache import get_cache
from app.db.models import (
    ModelVersion,
    Player,
    PlayerArchetype,
    PlayerImpactEstimate,
    RosterEntry,
    Standing,
    TeamNeed,
)
from app.domain.mandate import COMPONENT_KEYS, STRATEGY_WEIGHTS

# The strategy weight table is owned by `app.domain.mandate` — where each strategy
# also carries the sentence that explains what it optimises for — and re-exported
# here so every existing consumer and every stored evaluation keeps its meaning.
DEFAULT_WEIGHTS: dict[str, dict[str, float]] = STRATEGY_WEIGHTS

TEI_SIGMA_DEFAULT = 1.5  # index points; refined by per-player bands when available


# The reference asset the assets component is anchored on, unchanged from the flat
# 8-points-per-pick R4 shipped: a mid-first-rounder is worth 8 composite points. What
# changed is that every other pick is now priced RELATIVE to it by the fitted curve,
# instead of every pick being worth the same 8 points.
PICK_POINTS_PER_REFERENCE = 8.0
REFERENCE_PICK_VALUE = relative_pick_value(REFERENCE_SLOT)


# How many rotation rows the CHART shows. A display choice, deliberately independent of
# `projection.ROTATION_DEPTH`, which is a basketball claim about who matters.
ROTATION_VIEW_SIZE = 12


def _rotation_view(detail: list[dict], must_include: set[str]) -> list[dict]:
    """The rotation rows the UI charts: the top 12 by minutes, plus everyone in the deal.

    QA-6: `detail[:12]` sliced in **roster order**, not by minutes, so the chart read
    "Josh Giddey 20.4 → 0.0; Jalen Smith 0.0 → 12.4" for a Giddey-for-Curry trade —
    Curry absent, and a fabricated change for a player who was not in the deal, because
    removing one player shifted the index alignment between the two lists.

    Sorting fixes the ordering; `must_include` fixes the omission, because an acquired
    bench player can legitimately fall outside the top 12 and still be the whole point of
    the chart.
    """
    ordered = sorted(detail, key=lambda row: row["minutes"], reverse=True)
    keep = {row["player_id"] for row in ordered[:ROTATION_VIEW_SIZE]} | must_include
    # Re-filtered from `ordered` rather than appended, so an included player outside the
    # top 12 lands at their real position in the chart instead of on the end.
    return [row for row in ordered if row["player_id"] in keep]


def _is_illegal(legality: dict, team_id: str) -> bool:
    """True when the trade fails an implemented rule, for this team or as a whole.

    A trade that is illegal for any participant cannot be executed by anyone, so the
    overall verdict gates every team's score, not just the offending one's."""
    if legality.get("overall_status") == "verified_illegal":
        return True
    return legality.get("teams", {}).get(team_id, {}).get("status") == "verified_illegal"


@dataclass
class PlayerCard:
    """Every modelled quantity is optional.

    A player with no `PlayerImpactEstimate` used to arrive with `tei = 0.0`,
    `availability = 0.75` and `minutes = 12.0`. None of those is a measurement, and
    `tei = 0.0` is the **63rd percentile** of rostered players — a silent default more
    favourable than the −0.293 league mean suggests, and one that directly contradicts
    `docs/model-card-player-impact.md`. Absent values are now absent.
    """

    player_id: str
    name: str
    tei: float | None
    tei_sigma: float | None
    availability: float | None
    minutes: float | None
    age: float | None
    skills: dict[str, float]

    @property
    def is_modeled(self) -> bool:
        return self.tei is not None


class EvaluationService:
    """Builds per-team evaluations for a trade under a scenario."""

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()
        #: R8.2. Membership is read from the roster season; statistics, archetypes and team needs
        #: stay on the performance season (`settings.current_season`). One query, once per service.
        self.roster_season = resolve_roster_season(db)
        self._impact_cache: dict[str, PlayerImpactEstimate] | None = None
        self._skills_cache: dict[str, dict[str, float]] | None = None
        self._wins_mapping: dict | None = None
        # Per-service memoization. `generate_candidates` builds one service and then
        # evaluates 400 trades against the same handful of rosters, so these were
        # re-queried hundreds of times for identical answers.
        self._roster_cache: dict[str, list[PlayerCard]] = {}
        self._needs_cache: dict[str, dict[str, float]] = {}
        self._win_pct_cache: dict[str, float] | None = None
        self._pick_curve_cache: dict[str, float] | None = None
        self._roles_cache: dict[str, str] | None = None
        self._season_basis_cache: dict | None = None

    # ------------------------------------------------------------- data loading

    def _impacts(self) -> dict[str, PlayerImpactEstimate]:
        if self._impact_cache is None:
            model = self.db.scalar(
                select(ModelVersion).where(
                    ModelVersion.model_name == "player_impact", ModelVersion.is_active
                )
            )
            rows: list[PlayerImpactEstimate] = []
            if model is not None:
                rows = list(
                    self.db.scalars(
                        select(PlayerImpactEstimate).where(
                            PlayerImpactEstimate.model_version_id == model.id
                        )
                    ).all()
                )
            self._impact_cache = {r.player_id: r for r in rows}
        return self._impact_cache

    def _skills(self) -> dict[str, dict[str, float]]:
        if self._skills_cache is None:
            cache = get_cache()
            # The data-version namespace alone is bumped by ingestion, never by a
            # deploy, so a release that changes the skill contract would keep serving the
            # previous shape for the rest of the six-hour TTL. The fingerprint closes it.
            key = cache.versioned_key("skills", skill_schema_fingerprint())
            cached = cache.get_json(key)
            if cached:
                self._skills_cache = cached
            else:
                season_df = build_player_season_features(self.db)
                weighted = recency_weighted_features(season_df, self.settings.history_season_list)
                skills = {}
                for _, row in weighted.iterrows():
                    skills[row["player_id"]] = player_skill_vector(row, weighted)
                self._skills_cache = skills
                cache.set_json(key, skills, ttl_seconds=6 * 3600)
        return self._skills_cache

    def wins_mapping(self) -> dict:
        if self._wins_mapping is None:
            model = self.db.scalar(
                select(ModelVersion).where(
                    ModelVersion.model_name == "team_projection", ModelVersion.is_active
                )
            )
            self._wins_mapping = (
                model.validation_metrics
                if model
                else {"slope": 2.7, "intercept": 41.0, "calibrated": False}
            )
        return self._wins_mapping

    def _card(self, player: Player, roster_age: float | None = None) -> PlayerCard:
        impact = self._impacts().get(player.id)
        tei: float | None = float(impact.tei) if impact else None
        sigma: float | None = None
        if impact:
            sigma = (
                max((impact.tei_high - impact.tei_low) / 2.5631, 0.4)
                if impact.tei_high is not None and impact.tei_low is not None
                else TEI_SIGMA_DEFAULT
            )
        age: float | None = roster_age
        if age is None and player.birth_date:
            age = (date.today() - player.birth_date).days / 365.25
        return PlayerCard(
            player_id=player.id,
            name=player.full_name,
            tei=tei,
            tei_sigma=sigma,
            availability=(
                float(impact.availability) if impact and impact.availability is not None else None
            ),
            minutes=(
                float(impact.minutes_estimate)
                if impact and impact.minutes_estimate is not None
                else None
            ),
            age=age,
            skills=self._skills().get(player.id, {}),
        )

    def _roster_cards(self, team_id: str) -> list[PlayerCard]:
        if team_id in self._roster_cache:
            return self._roster_cache[team_id]
        entries = self.db.scalars(
            select(RosterEntry)
            .where(
                RosterEntry.team_id == team_id,
                current_roster_clause(self.roster_season),
            )
            # Without an explicit order this returned whatever the database happened to
            # produce, so every `[:12]` downstream was an arbitrary 12 rather than a
            # top 12 — and the order changed between runs on Postgres.
            .order_by(RosterEntry.player_id)
        ).all()
        cards = [self._card(e.player, e.age) for e in entries]
        self._roster_cache[team_id] = cards
        return cards

    def _all_rosters(self) -> dict[str, list[PlayerCard]]:
        """Every current roster in **one** query, populating the per-team cache.

        `_league_role_reference` needs all thirty rotations. Calling `_roster_cards`
        thirty times costs thirty round trips, which on the live database took a cold
        `/trades/evaluate` from 6 queries to 37 — a real cost on Postgres, and one paid
        by the *first* request after every ingestion. It also passed
        `test_query_budget.py` unnoticed, because that fixture holds two teams.
        """
        entries = self.db.scalars(
            select(RosterEntry)
            .where(current_roster_clause(self.roster_season))
            .order_by(RosterEntry.team_id, RosterEntry.player_id)
        ).all()
        grouped: dict[str, list[PlayerCard]] = {}
        for entry in entries:
            grouped.setdefault(entry.team_id, []).append(self._card(entry.player, entry.age))
        for team_id, cards in grouped.items():
            self._roster_cache.setdefault(team_id, cards)
        return grouped

    def _team_needs(self, team_id: str) -> dict[str, float]:
        if team_id not in self._needs_cache:
            rows = self.db.scalars(
                select(TeamNeed).where(
                    TeamNeed.team_id == team_id, TeamNeed.season == self.settings.current_season
                )
            ).all()
            self._needs_cache[team_id] = {r.need_key: r.severity for r in rows}
        return self._needs_cache[team_id]

    def season_basis(self) -> dict:
        """R8.2. Which season each input of an evaluation comes from, and what that limits.

        The roster is read from the roster season; player impact, skills and roles come from the
        performance season's statistics; team needs were measured on the performance-season *team*.
        When the roster has turned over since, the first three still describe players correctly
        (they are properties of the player), but needs do not describe the new roster. Said here
        rather than silently scored.
        """
        if self._season_basis_cache is None:
            performance = self.settings.current_season
            estimate_seasons = sorted({r.season for r in self._impacts().values()})
            differs = self.roster_season != performance
            limitations: list[dict] = []
            if differs:
                limitations.append(
                    {
                        "component": "fit",
                        "reason": "team_needs_describe_previous_team",
                        "message": (
                            f"Team needs were measured on the {performance} team (its statistics "
                            f"and roster); the roster is {self.roster_season}. Anything scored from "
                            "those needs (roster fit) uses them unchanged and may not describe "
                            "this roster."
                        ),
                    }
                )
            if estimate_seasons and performance not in estimate_seasons:
                # The statistics season has moved on (the rollover) but `make train && make score`
                # has not been run for it: the active model's estimates are an earlier season's.
                limitations.append(
                    {
                        "component": "performance",
                        "reason": "impact_estimates_from_earlier_season",
                        "message": (
                            f"The active impact model has no estimates for {performance}; player "
                            f"impact is read from its {', '.join(estimate_seasons)} estimates. Run "
                            "`make train && make score` to produce them."
                        ),
                    }
                )
            self._season_basis_cache = {
                "roster_season": self.roster_season,
                "performance_season": performance,
                "impact_estimate_seasons": estimate_seasons,
                "team_needs_season": performance,
                "contract_season": self.settings.cap_league_year,
                "roster_differs_from_performance": differs,
                "limitations": limitations,
            }
        return self._season_basis_cache

    # ------------------------------------------------------------- components

    def _performance(
        self, roster: list[PlayerCard], incoming: list[PlayerCard], outgoing_ids: set[str]
    ) -> tuple[float | None, dict]:
        """Players with no impact estimate are excluded from the rotation and named.

        The alternative — giving them `tei = 0.0` and 12 baseline minutes — put a player
        with zero data at the 63rd percentile of the league and let them move the
        projection. Excluding them mirrors the deliberate `min_total_minutes = 200`
        exclusion already in the feature pipeline; the difference is that this one is
        *visible*, and the roster count still includes them.
        """

        def to_rotation(cards: list[PlayerCard]) -> list[RotationPlayer]:
            rotation: list[RotationPlayer] = []
            for c in cards:
                if c.tei is None or c.minutes is None:
                    continue
                rotation.append(
                    RotationPlayer(
                        player_id=c.player_id,
                        name=c.name,
                        tei=c.tei,
                        baseline_minutes=c.minutes,
                        # An unknown availability is not a discount; it is no discount,
                        # and the risk component reports that it could not be measured.
                        availability=1.0 if c.availability is None else c.availability,
                    )
                )
            return rotation

        after_cards = [c for c in roster if c.player_id not in outgoing_ids] + incoming
        unmodeled = sorted(
            {c.name for c in roster + incoming if c.tei is None or c.minutes is None}
        )

        before_rotation = to_rotation(roster)
        after_rotation = to_rotation(after_cards)
        # An EMPTY post-trade rotation is not an unmodellable roster — it is a roster of
        # 240 replacement-level minutes, which is exactly what `allocate_rotation` now
        # models (R3-3). Only a team with nothing to compare against is unprojectable.
        if not before_rotation:
            return None, {
                "unavailable": "no player on this roster has an impact estimate, so the "
                "rotation cannot be projected",
                "unmodeled_players": unmodeled,
            }

        before = allocate_rotation(before_rotation)
        # R5.5: the after-roster is priced AGAINST the before-allocation, not allocated
        # from scratch. Re-allocating independently re-shared a departure's minutes
        # across everyone who stayed, so a team could be scored as improving by giving a
        # rotation player away — 191 of 370 above-replacement players, measured. With the
        # anchor supplied the freed minutes are replacement minutes, which is what
        # `REPLACEMENT_TEI` already means. See `projection.ABSORPTION_RULE`.
        after = allocate_rotation(after_rotation, anchor=before.minutes)
        # R3-5: one definition of delta_net. The point estimate and the Monte Carlo must
        # compute the same quantity through the same conversion, or they disagree by the
        # size of the coefficient.
        delta_net = team_tei_to_net_rating_delta(before, after)
        delta_wins = net_rating_delta_to_wins(delta_net, self.wins_mapping())
        # 5 points per projected win — ±10 wins still spans the scale as documented, but
        # through `bounded_score`, so a +15-win deal no longer scores identically to a
        # +10-win one (see analytics/components.py).
        score = bounded_score(50.0 + delta_wins * 5.0)
        return score, {
            "delta_net_rating": round(delta_net, 2),
            "delta_wins": round(delta_wins, 2),
            "rotation_before": _rotation_view(before.detail, outgoing_ids),
            "rotation_after": _rotation_view(after.detail, {c.player_id for c in incoming}),
            "wins_mapping": self.wins_mapping(),
            "unmodeled_players": unmodeled,
            "modeled_players_after": len(after_rotation),
            "roster_players_after": len(after_cards),
            # The one allocation both the point estimate and the simulation read (R3-5).
            "_rotations": (before, after),
        }

    def _fit(
        self,
        team_id: str,
        roster: list[PlayerCard],
        incoming: list[PlayerCard],
        outgoing: list[PlayerCard],
    ) -> tuple[float | None, dict]:
        needs = self._team_needs(team_id)
        if not needs:
            return None, {"unavailable": "team needs not computed; run `make score`"}

        # Fit compares the arriving package against the departing one. R1 found the old
        # code substituting a flat 50th-percentile player for an empty side — so trading
        # everyone away for nothing scored as if a median NBA rotation player had been
        # acquired in every skill — and withheld the component rather than invent a
        # number. R5 recorded that it would supply a measured baseline and did not.
        #
        # R5.5 supplies it, and the two sides get DIFFERENT baselines for the same reason
        # the allocator's two directions differ (see `projection.ABSORPTION_RULE`):
        #
        #   nothing arriving   the departing player's minutes are played by a REPLACEMENT
        #                      player, so the arriving package is `REPLACEMENT_SKILLS` —
        #                      measured on the population REPLACEMENT_TEI is fitted on.
        #   nothing departing  the arriving player's minutes are taken from the
        #                      INCUMBENTS proportionally, so the departing package is this
        #                      roster's own minutes-weighted skill profile. That is R5-1b's
        #                      construction for `risk`, applied to skills.
        #
        # Both substitutions are disclosed under `baseline_note` and `baseline_used`; the
        # component is still withheld when neither side has a measurable player at all.
        #
        # Measured on 240 deals of each shape against the 30 ingested rosters: two-sided
        # deals are untouched (baseline `None` on all 240, mean 48.83), one-way giveaways
        # land below neutral 68.3 % of the time at mean 41.01, and the worst of them are
        # Michael Porter Jr., Jaren Jackson Jr. and Myles Turner — real rotation players.
        #
        # **What this score does not say is how MUCH changes.** `fit_score` normalises
        # minutes within each side, so a package's weight cancels and the component
        # measures the fit of the change rather than its size: acquiring an 8-minute
        # player who answers a need scores like acquiring a 32-minute one. That is
        # pre-existing behaviour on two-sided deals, it is why `performance` and not `fit`
        # carries magnitude, and re-normalising it would be a re-tuning of the composite
        # that no measurement here motivates.
        incoming_entries = [(c.skills, c.minutes) for c in incoming if c.skills and c.minutes]
        outgoing_entries = [(c.skills, c.minutes) for c in outgoing if c.skills and c.minutes]
        if not incoming_entries and not outgoing_entries:
            return None, {
                "unavailable": (
                    "roster fit compares the arriving package with the departing one; "
                    "neither side of this deal has a player with a skill profile and "
                    "minutes estimate, so there is nothing to compare"
                ),
                "needs": needs,
            }

        with_minutes = [c for c in roster if c.minutes is not None]
        baseline_note: str | None = None
        baseline_used: str | None = None
        if not incoming_entries:
            weight = sum(w for _, w in outgoing_entries)
            incoming_entries = [(dict(REPLACEMENT_SKILLS), weight)]
            baseline_used = "replacement"
            baseline_note = (
                "nothing arrives, so the departing minutes are priced at a "
                f"replacement player's measured skill profile ({REPLACEMENT_SKILL_RULE})"
            )
        elif not outgoing_entries:
            roster_profile = self._roster_skill_profile(roster, exclude=set())
            if roster_profile is None:
                return None, {
                    "unavailable": (
                        "nothing departs, so the arriving minutes come out of this "
                        "roster — and no rostered player has both a skill profile and a "
                        "minutes estimate, so there is nothing to price them against"
                    ),
                    "needs": needs,
                }
            weight = sum(w for _, w in incoming_entries)
            outgoing_entries = [(roster_profile, weight)]
            baseline_used = "roster"
            baseline_note = (
                "nothing departs, so the arriving minutes are priced against this "
                "roster's own minutes-weighted skill profile, because that is who "
                "gives them up"
            )

        # ROTATION_DEPTH, not a local 9: the depth at which a roster counts as strong in
        # a skill must match the depth at which `REPLACEMENT_TEI` says a player becomes
        # replaceable (R4-4).
        top_rotation = sorted(with_minutes, key=lambda c: c.minutes or 0.0, reverse=True)[
            :ROTATION_DEPTH
        ]
        roster_strengths: dict[str, float | None] = {}
        for key in SKILL_KEYS:
            values = sorted(c.skills[key] for c in top_rotation if c.skills and key in c.skills)
            # Third-best is the "already strong here" threshold; with fewer than three
            # observations the roster's strength in that skill is unknown, not average.
            roster_strengths[key] = values[-3] if len(values) >= 3 else None

        score, detail = fit_score(
            needs=needs,
            incoming=incoming_entries,
            outgoing=outgoing_entries,
            roster_strengths=roster_strengths,
            need_to_skill=NEED_TO_SKILL,
        )
        # Attach the reason a measured need has no player-side answer, so the UI can say
        # why rather than silently omitting a weakness the same page reports (R4-2).
        withheld = {
            key: UNADDRESSABLE_NEEDS[key]
            for key in detail.get("needs_without_a_skill", [])
            if key in UNADDRESSABLE_NEEDS
        }
        return bounded_score(50.0 + score * 120.0), {
            **detail,
            "raw_fit": round(score, 4),
            "needs_not_addressable": withheld,
            "needs": needs,
            "needs_season": self.settings.current_season,
            "needs_match_roster": self.roster_season == self.settings.current_season,
            # `None` on an ordinary two-sided deal. Named rather than inferred, so a
            # one-way score can never be read as if both packages were measured.
            "baseline_used": baseline_used,
            **({"baseline_note": baseline_note} if baseline_note else {}),
        }

    def _roles(self) -> dict[str, str]:
        """Player role labels for the current season, from `player_archetypes`.

        `role_id` comes from a frozen append-only map (R4-3), so the label is stable
        across retrains and can be compared between seasons.
        """
        if self._roles_cache is None:
            rows = self.db.scalars(
                select(PlayerArchetype).where(
                    PlayerArchetype.season == self.settings.current_season
                )
            ).all()
            self._roles_cache = {r.player_id: r.label for r in rows}
        return self._roles_cache

    def _league_role_reference(self) -> dict[str, dict[str, float]]:
        """Each role's league median and congestion threshold, over the 30 rosters.

        Cached under the data-version namespace, like the skill vectors: it needs one
        rotation allocation per team, and a request that evaluates a trade has no reason
        to redo the whole league every time.
        """
        cache = get_cache()
        # Keyed by BOTH seasons: the reference is built from rosters (roster season) and role
        # labels (performance season), and either can turn over without the other.
        key = cache.versioned_key(
            "role_minutes", self.settings.current_season, self.roster_season
        )
        cached = cache.get_json(key)
        if cached:
            return cached
        roles = self._roles()
        per_team: list[dict[str, float]] = []
        for cards in self._all_rosters().values():
            rotation = [
                RotationPlayer(
                    player_id=c.player_id,
                    name=c.name,
                    tei=c.tei,
                    baseline_minutes=c.minutes,
                    availability=1.0 if c.availability is None else c.availability,
                )
                for c in cards
                if c.tei is not None and c.minutes is not None
            ]
            if not rotation:
                continue
            per_team.append(role_minutes(allocate_rotation(rotation).minutes, roles))
        reference = league_role_reference(per_team)
        cache.set_json(key, reference, ttl_seconds=6 * 3600)
        return reference

    def _roster_shape(
        self,
        rotations: tuple[RotationResult, RotationResult] | None,
        incoming: list[PlayerCard],
    ) -> dict:
        """Role minutes before and after, against the league's own distribution."""
        if rotations is None:
            return {
                "unavailable": (
                    "the rotation could not be projected, so its shape cannot be "
                    "reported either"
                )
            }
        before, after = rotations
        return shape_report(
            before.minutes,
            after.minutes,
            self._roles(),
            self._league_role_reference(),
            {c.player_id for c in incoming},
        )

    def _roster_skill_profile(
        self, roster: list[PlayerCard], exclude: set[str]
    ) -> dict[str, float] | None:
        """The roster's own minutes-weighted skill profile.

        Used as the departing package when nothing departs: an arriving player's minutes
        come out of the incumbents, proportionally to what they hold, so the skills he
        displaces are the incumbents' own. Weighted by minutes for the same reason
        `_weighted_availability` is — thirty minutes of a starter displaces more than
        eight of a reserve.
        """
        entries = [
            (c.skills, c.minutes)
            for c in roster
            if c.skills and c.minutes and c.player_id not in exclude
        ]
        total = sum(w for _, w in entries)
        if not entries or total <= 0:
            return None
        profile: dict[str, float] = {}
        for key in SKILL_KEYS:
            weighted = [(s[key], w) for s, w in entries if key in s]
            covered = sum(w for _, w in weighted)
            # A skill only enters when it is measured, on the same principle `fit_score`
            # applies to the packages: an unmeasured skill is not a median one.
            if covered > 0:
                profile[key] = sum(v * w for v, w in weighted) / covered
        return profile or None

    def _contract_value(
        self,
        incoming: list[PlayerCard],
        outgoing: list[PlayerCard],
        salaries: dict[str, int | None],
        cap: int,
    ) -> tuple[float | None, dict]:
        """Cap-dollar-per-impact heuristic (documented in docs/methodology.md).
        Returns None — excluded from the composite — when any salary is unknown."""
        cards = incoming + outgoing
        if any(salaries.get(c.player_id) is None for c in cards):
            return None, {
                "unavailable": "contract data missing; contract-value component excluded "
                "and weights renormalized"
            }
        unmodeled = sorted({c.name for c in cards if c.tei is None})
        if unmodeled:
            return None, {
                "unavailable": "contract value compares salary against modelled impact, "
                "and these players have no impact estimate: " + ", ".join(unmodeled)
            }

        def market_share(tei: float) -> float:
            # replacement ≈ minimum deal (~2.5% of cap); league-average rotation ≈ 8%;
            # star (+5) ≈ 25%; ceiling 35% (max contract)
            return max(0.025, min(0.35, 0.08 + 0.034 * tei))

        def surplus(cards_: list[PlayerCard]) -> float:
            total = 0.0
            for c in cards_:
                actual = (salaries.get(c.player_id) or 0) / cap
                assert (
                    c.tei is not None
                )  # guarded above: unmodelled players withhold this component
                total += market_share(c.tei) - actual
            return total

        net_surplus = surplus(incoming) - surplus(outgoing)  # in cap-share units
        return bounded_score(50.0 + net_surplus * 250.0), {
            "net_surplus_cap_share": round(net_surplus, 4),
            "method": "cap-dollar-per-impact heuristic (no historical salary model data)",
        }

    def _timeline(
        self, strategy: str, incoming: list[PlayerCard], outgoing: list[PlayerCard]
    ) -> tuple[float | None, dict]:
        unweighted_sides: list[str] = []

        def alignment(cards: list[PlayerCard], label: str) -> float | None:
            aged = [c for c in cards if c.age]
            if not aged:
                return None
            # Minutes weight this when it is known. When it is not, the average is
            # unweighted and the response says so — an assumed 12 minutes would be a
            # measurement the pipeline never made.
            if all(c.minutes is None for c in aged):
                unweighted_sides.append(label)
                return sum(timeline_alignment(c.age or 0.0, strategy) for c in aged) / len(aged)
            weighted = [
                (timeline_alignment(c.age or 0.0, strategy), c.minutes)
                for c in aged
                if c.minutes is not None
            ]
            total_weight = sum(w for _, w in weighted)
            if total_weight <= 0:
                unweighted_sides.append(label)
                return sum(timeline_alignment(c.age or 0.0, strategy) for c in aged) / len(aged)
            return sum(a * w for a, w in weighted) / total_weight

        align_in = alignment(incoming, "incoming")
        align_out = alignment(outgoing, "outgoing")
        if align_in is None or align_out is None:
            return None, {"unavailable": "player ages missing"}
        detail: dict[str, Any] = {
            "strategy": strategy,
            "incoming_alignment": round(align_in, 3),
            "outgoing_alignment": round(align_out, 3),
        }
        if unweighted_sides:
            detail["weighting_note"] = (
                "no minutes estimate for "
                + " and ".join(unweighted_sides)
                + "; alignment is an unweighted average"
            )
        return bounded_score(50.0 + (align_in - align_out) * 100.0), detail

    def _pick_terms(self, team_id: str, move: dict) -> tuple[PickTerms, str]:
        """Turn a pick move into valuation terms, using ownership rows where they exist.

        The move names the team *trading* the pick, which is not necessarily the team
        whose record sets the slot. A reconciled ownership row supplies the original team
        and the verification; without one the pick is unverified and says so.
        """
        rows = resolver.draft_pick_rows(self.db)
        holder = move.get("from_team_id")
        match = next(
            (
                r
                for r in rows
                if r.draft_year == move["draft_year"]
                and r.round_number == move["round_number"]
                and r.owning_team_id == holder
            ),
            None,
        )
        original_team_id = match.original_team_id if match else holder
        verified = bool(match.is_verified) if match else False
        if match is None and not move.get("is_hypothetical", True):
            verified = False
        label = f"{move['draft_year']} round {move['round_number']}"
        return (
            PickTerms(
                draft_year=int(move["draft_year"]),
                round_number=int(move["round_number"]),
                original_team_win_pct=self._team_win_pct(original_team_id),
                protections=move.get("protections") or (match.protections if match else None),
                is_conditional=bool(match and match.conveyance in ("swap", "conditional")),
                ownership_verified=verified,
                unresolved_reasons=(
                    (f"source records this pick's conveyance as {match.conveyance!r}",)
                    if match and not match.is_verified
                    else ()
                ),
            ),
            label,
        )

    def _pick_curve(self) -> dict[str, float] | None:
        """The registered pick-value curve, or `None` to use the committed constants.

        Served from `model_versions` for the same reason the R3 conversion is: a curve
        fitted on this database's draft classes is the one that describes this database,
        and a committed constant that silently outlives its refit is how train and serve
        drift apart (C5).
        """
        if self._pick_curve_cache is None:
            model = self.db.scalar(
                select(ModelVersion).where(
                    ModelVersion.model_name == "pick_value_curve", ModelVersion.is_active
                )
            )
            metrics = (model.validation_metrics if model else None) or {}
            curve = metrics.get("curve") if metrics.get("calibrated") else None
            self._pick_curve_cache = curve or {}
        return self._pick_curve_cache or None

    def _team_win_pct(self, team_id: str | None) -> float | None:
        """Most recent measured win percentage, memoized per service."""
        if team_id is None:
            return None
        if self._win_pct_cache is None:
            self._win_pct_cache = {}
            for row in self.db.scalars(select(Standing).order_by(Standing.season)).all():
                self._win_pct_cache[row.team_id] = row.win_pct
        return self._win_pct_cache.get(team_id)

    def _assets(
        self,
        team_id: str,
        pick_moves: list[dict],
        payroll_delta: int | None,
        payroll_exact: bool,
        roster_spots_delta: int,
    ) -> tuple[float | None, dict]:
        """**Draft capital.** Withheld when no pick in the deal can be priced.

        The measured defect this replaces: `assets` counted picks (8 points each,
        regardless of which pick) and read payroll from `team_legality.payroll_before`,
        which is `None` unless *every* rostered player is priced — 0 of 30 teams under the
        available contract data. Over 482 scored evaluations it therefore took only three
        values, {48, 50, 52}, from the roster-spot term alone, and contributed **−0.006**
        of composite variance while holding 15 % of the weight. A component that cannot
        move is not a conservative component; it is a placebo diluting the ones that can.

        Picks are now valued by the empirical curve (`analytics.picks`) rather than
        counted, so a 2027 unprotected first is no longer interchangeable with a 2031
        second, and a pick the curve refuses to price — protected, swapped, or of
        unverified ownership — is **not** midpointed into the score. It is listed with the
        range it would have spanned, because averaging over conditions nothing here can
        price is exactly how a conditional pick acquires a false decimal.

        **Payroll change is reported and not scored, and that was a measurement, not a
        preference.** A first pass computed the delta from the moved players' own salaries
        — which is exact whenever those players are priced, a far weaker requirement than
        pricing two rosters — and scored it here. Measured over the resulting 168
        fully-scored evaluations, `assets` then correlated **0.837** with `contract`
        (0.779 Spearman), because with no pick moving both components reduce to the same
        salary delta. Removing one double count and introducing another is not progress.
        `contract` divides salary by impact, which is the question it asks; this component
        is draft capital, which is a different asset class. The delta stays in the detail
        because it is genuinely useful; it is simply not scored twice.

        The consequence, stated plainly: **on a player-only trade this component is
        withheld**, and the composite renormalises without it. That is the honest answer.
        Without pick data there is no measurable asset content in a player-only deal that
        `contract` does not already price.
        """
        detail: dict[str, Any] = {
            "picks_in": len([m for m in pick_moves if m["to_team_id"] == team_id]),
            "picks_out": len([m for m in pick_moves if m["from_team_id"] == team_id]),
            "roster_spots_delta": roster_spots_delta,
        }
        current_year = date.today().year

        valued_units = 0.0
        priced: list[dict] = []
        unpriced: list[dict] = []
        for move in pick_moves:
            if team_id not in (move["from_team_id"], move["to_team_id"]):
                continue
            terms, label = self._pick_terms(team_id, move)
            value = pick_value(terms, current_year, self._pick_curve())
            sign = 1.0 if move["to_team_id"] == team_id else -1.0
            row = {
                "pick": label,
                "direction": "in" if sign > 0 else "out",
                **value.as_dict(),
            }
            if value.precision == "interval" and value.point is not None:
                valued_units += sign * value.point / REFERENCE_PICK_VALUE
                priced.append(row)
            else:
                unpriced.append(row)

        detail["picks_priced"] = priced
        detail["picks_not_priced"] = unpriced
        detail["pick_units_net"] = round(valued_units, 4)
        detail["pick_reference"] = (
            f"1.0 unit = the empirical value of slot {REFERENCE_SLOT}, worth "
            f"{PICK_POINTS_PER_REFERENCE} composite points — the value a pick has always "
            "carried here, now applied per pick rather than per count"
        )

        # Payroll change is REPORTED here and SCORED by `contract`, which is the component
        # that owns salary. Scoring it in both was measured and rejected: with the payroll
        # term in, `assets` correlated **0.837** with `contract` (0.779 Spearman) over 168
        # fully-scored evaluations, because with no pick moving both components reduce to
        # the same salary delta. Removing one double count and introducing another is not
        # progress. `contract` divides salary by impact, which is the question it asks;
        # `assets` is draft capital, which is a different asset class.
        if payroll_delta is not None:
            detail["payroll_delta"] = payroll_delta
            detail["payroll_basis"] = (
                "sum of the moved players' salaries for the cap league year"
                if payroll_exact
                else "partial — some moved players have no salary on file"
            )
        else:
            detail["payroll_note"] = (
                "payroll delta unknown: at least one player in this deal has no salary "
                "for the cap league year, so the change cannot be computed"
            )
        detail["payroll_scored"] = False
        detail["payroll_scored_note"] = (
            "reported, not scored — the contract component prices salary against impact, "
            "and scoring the raw delta here as well made the two components 0.837 "
            "correlated over 168 evaluations"
        )

        if not priced:
            detail["unavailable"] = (
                "No draft pick in this deal could be priced, so there is no draft capital "
                "to score. The roster-spot term alone spans four points and cannot express "
                "asset value, so scoring it would put a near-constant in the composite."
            )
            return None, detail

        score = 50.0 + PICK_POINTS_PER_REFERENCE * valued_units - 2.0 * roster_spots_delta
        if unpriced:
            detail["precision_note"] = (
                f"{len(unpriced)} pick(s) in this deal are protected, swapped or of "
                "unverified ownership. They are excluded from the score and listed with "
                "the range they would have spanned; the component understates a package "
                "that includes them."
            )
        return bounded_score(score), detail

    @staticmethod
    def _weighted_availability(cards: list[PlayerCard]) -> tuple[float | None, int]:
        """Minutes-weighted availability of a package, and how many players it measures.

        Minutes-weighted rather than a flat mean: thirty minutes of a 60 %-available
        starter is far more exposure than eight minutes of one. A player with no
        availability record contributes nothing to either sum instead of a default.
        """
        pairs = [
            (c.availability, c.minutes)
            for c in cards
            if c.availability is not None and c.minutes is not None and c.minutes > 0
        ]
        if not pairs:
            return None, 0
        total = sum(m for _, m in pairs)
        return sum(a * m for a, m in pairs) / total, len(pairs)

    def _risk(
        self,
        roster: list[PlayerCard],
        incoming: list[PlayerCard],
        outgoing: list[PlayerCard],
        legality: dict,
        team_id: str,
    ) -> tuple[float | None, dict]:
        """Availability exposure — **the one thing here that performance does not price**.

        Until R5 the dominant term was `prob_positive`, the Monte Carlo's probability that
        Δwins > 0. That is the performance component restated as a probability, and the
        composite counted it twice. Measured over 482 scored evaluations of the post-R4
        engine on the 30 ingested rosters:

            corr(prob_positive, performance)   0.913
            corr(risk, performance)            0.851  Pearson / 0.937 Spearman
            risk's share of composite variance 0.244

        So a quarter of the composite's variance came from a component that was 85–94 %
        the same quantity as another one, and `performance`'s effective weight was roughly
        double what the weight vector said. C12 called folding risk into performance
        backwards; the fix is to take performance back out of risk.

        What is left is genuinely orthogonal, measured on the same sample:

            corr(Δ minutes-weighted availability, performance)   0.136

        The score is the **change** in the availability of the minutes involved, not the
        level of the incoming package's. A level answers "are these players durable",
        which is not a question about the trade; the change answers "is the team taking on
        more games-missed exposure than it is shedding", which is. Availability is a share
        of games in 0..1, so the difference is bounded on [−1, 1] and maps affinely onto
        the full 0..100 scale — this component is not squashed, because both endpoints
        mean something.

        **Where a side is empty the baseline is the roster's own measured availability**,
        not an invented one: minutes not spent on an arriving player are spent by the
        players already there, and their weighted availability is a measurement this
        pipeline already makes. When even that is unmeasurable the component is withheld.

        `prob_positive` is not lost — it is still computed, still returned under
        `uncertainty`, and still drives the displayed interval. It is simply no longer
        scored a second time.
        """
        detail: dict[str, Any] = {}

        # Reported, never scored — see `legality_verification` below.
        team_rules = [
            r for r in legality.get("rule_results", []) if r.get("team_id") in (team_id, None)
        ]
        definite = [r for r in team_rules if r.get("status") in ("pass", "fail", "warning")]
        detail["legality_verification"] = {
            "rules_evaluated": len(team_rules),
            "rules_with_a_definite_verdict": len(definite),
            "share": round(len(definite) / len(team_rules), 4) if team_rules else None,
            "scored": False,
            "note": (
                "How much of the implemented CBA check reached a verdict for this team. "
                "Reported, never scored: measured over 482 evaluations it runs 0.063 "
                "± 0.071 with a ceiling of 0.143, and what moves it is which contract "
                "fields the configured provider supplies — a property of the dataset, "
                "not of the deal. Scoring it would add a near-constant offset."
            ),
        }

        roster_availability, roster_n = self._weighted_availability(roster)
        measured_in, n_in = self._weighted_availability(incoming)
        measured_out, n_out = self._weighted_availability(outgoing)

        # These two fields mean exactly what they say: the measured availability of that
        # package. They stay `None` for a side with no player carrying the measurement —
        # QA-8 was a report line reading "Historical availability of incoming players:
        # 85 %" for a deal with no incoming players, and a substituted baseline under this
        # key would restore that defect with a different number.
        detail["incoming_availability"] = round(measured_in, 3) if measured_in is not None else None
        detail["outgoing_availability"] = (
            round(measured_out, 3) if measured_out is not None else None
        )
        detail["incoming_availability_players"] = n_in
        detail["outgoing_availability_players"] = n_out
        detail["roster_availability"] = (
            round(roster_availability, 3) if roster_availability is not None else None
        )
        detail["roster_availability_players"] = roster_n

        avail_in = measured_in if measured_in is not None else roster_availability
        avail_out = measured_out if measured_out is not None else roster_availability
        substituted = [
            label
            for label, measured in (("incoming", measured_in), ("outgoing", measured_out))
            if measured is None
        ]
        if substituted and roster_availability is not None:
            detail["baseline_note"] = (
                "no player with a measured availability on the "
                + " or ".join(substituted)
                + " side; those minutes are priced at this roster's own measured "
                "availability, because that is who plays them"
            )

        if avail_in is None or avail_out is None:
            detail["unavailable"] = (
                "no player on either side of this deal, or on the roster it is evaluated "
                "against, has a measured availability, so exposure cannot be scored"
            )
            return None, detail

        delta = avail_in - avail_out
        detail["availability_delta"] = round(delta, 4)
        detail["method"] = (
            "minutes-weighted availability of the arriving package minus the departing "
            "one, on [-1, 1], mapped affinely to 0..100"
        )
        return affine_score(delta, -1.0, 1.0), detail

    # ------------------------------------------------------------- suppression

    def _suppressed_for_illegality(
        self,
        team_id: str,
        team_legality: dict,
        legality: dict,
        weights: dict[str, float],
        incoming: list[PlayerCard],
        outgoing: list[PlayerCard],
    ) -> dict:
        """The response shape is unchanged; every score-bearing field is null.

        Keeping the keys means no consumer has to branch on their absence, and
        `decision_status` distinguishes "we refuse to score this" from "we could not
        score this" — two different `composite_utility: null` cases that the old contract
        could not tell apart."""
        failing = [
            {
                "rule_code": r["rule_code"],
                "team_id": r["team_id"],
                "message": r["message"],
                "calculation": r.get("calculation", {}),
                "source_reference": r.get("source_reference", ""),
            }
            for r in legality.get("rule_results", [])
            if r.get("status") == "fail"
        ]
        unmodeled_players = sorted({c.name for c in incoming + outgoing if c.tei is None})
        return {
            "team_id": team_id,
            "legality": team_legality,
            "has_unmodeled_players": bool(unmodeled_players),
            "unmodeled_players": unmodeled_players,
            "decision_status": "suppressed_illegal",
            "suppression": {
                "reason": "verified_illegal",
                "message": "This trade fails at least one implemented CBA rule, so it "
                "cannot be executed as constructed. No decision score is reported for a "
                "deal that cannot happen.",
                "failing_rules": failing,
            },
            "composite_utility": None,
            "confidence": "not_applicable",
            "components": dict.fromkeys(COMPONENT_KEYS),
            "excluded_components": list(COMPONENT_KEYS),
            "drivers": [],
            "weights": weights,
            "detail": {},
            "uncertainty": {},
            "sensitivity_tornado": [],
            "incoming": [self._player_summary(c) for c in incoming],
            "outgoing": [self._player_summary(c) for c in outgoing],
            "season_basis": self.season_basis(),
            "membership_warnings": [],
            "evaluated_at": datetime.now(UTC).isoformat(),
        }

    def _membership_warnings(self, incoming: list[PlayerCard]) -> list[dict]:
        """Players arriving in the deal who are on no current roster in Pivot's snapshot.

        The trade builder deliberately lets such a player through — refusing him would block every
        offseason free agent — so without a label he is scored as if he were available to acquire.
        That is more common once the roster season moves on (87 players of the 2025-26 rosters are on
        none of the 2026-27 ones). Uncertain membership is named, never settled in either direction.
        """
        membership = resolver.current_roster(self.db, self.roster_season)
        return [
            {
                "player_id": card.player_id,
                "name": card.name,
                "message": (
                    f"{card.name} is on no {self.roster_season} roster in Pivot's snapshot (free agent, "
                    "waived, retired, or not yet listed). He is scored as available to acquire; "
                    "that is not confirmed."
                ),
            }
            for card in incoming
            if card.player_id not in membership
        ]

    @staticmethod
    def _player_summary(card: PlayerCard) -> dict:
        return {
            "player_id": card.player_id,
            "name": card.name,
            "tei": round(card.tei, 2) if card.tei is not None else None,
        }

    # ------------------------------------------------------------- entry point

    def evaluate_for_team(
        self,
        team_id: str,
        team_ids: list[str],
        player_moves: list[dict],
        pick_moves: list[dict],
        strategy: str = "custom",
        weights: dict[str, float] | None = None,
        legality: dict | None = None,
        simulate: bool = True,
    ) -> dict:
        """`simulate=False` skips the 2,000-draw Monte Carlo and says so in the response.

        Nothing scored reads the simulation. It produced `prob_positive`, which was the
        risk component until R5-1b removed it as a restatement of `performance`, and the
        interval the UI displays. The candidate generator reads neither: it needs the
        composite and `delta_wins`, both of which are point estimates. Profiled over one
        `generate` request, the simulation was **1.10 s of 1.83 s** — 60 % of the request
        spent computing a distribution nobody read. The `uncertainty` block is marked
        `skipped` rather than filled with zeros, because an all-zero draw array reports
        `prob_positive = 0.0`, which reads as "certain to hurt" (QA-5).
        """
        weights = normalize_weights(
            weights or DEFAULT_WEIGHTS.get(strategy, DEFAULT_WEIGHTS["custom"])
        )
        roster = self._roster_cards(team_id)
        incoming_ids = [m["player_id"] for m in player_moves if m["to_team_id"] == team_id]
        outgoing_ids = [m["player_id"] for m in player_moves if m["from_team_id"] == team_id]
        # Batched and session-cached: this was one `SELECT … FROM players WHERE id IN …`
        # per team per evaluation, i.e. 800 of the 815 queries a `/trades/generate`
        # request still issued after the salary batching.
        incoming = [
            self._card(player) for player in resolver.players(self.db, incoming_ids).values()
        ]
        outgoing = [c for c in roster if c.player_id in set(outgoing_ids)]

        if legality is None:
            context = build_trade_context(self.db, team_ids, player_moves, pick_moves)
            legality = TradeLegalityEngine().evaluate(context)
        team_legality = legality["teams"].get(team_id, {})

        # A trade that fails an implemented CBA rule cannot be executed, so there is no
        # decision to score. The failing rules and their dollar figures are still
        # returned — the refusal is explained, not silent.
        if _is_illegal(legality, team_id):
            return self._suppressed_for_illegality(
                team_id, team_legality, legality, weights, incoming, outgoing
            )

        # One batched lookup, and the imports are module-level: these two were
        # function-local imports of a private helper inside the hot path, executed about
        # 800 times per `/trades/generate` request.
        resolved = player_salaries(
            self.db, [c.player_id for c in incoming + outgoing], self.settings.cap_league_year
        )
        salaries: dict[str, int | None] = {pid: value[0] for pid, value in resolved.items()}

        cap_params = load_cap_params(self.db, self.settings.cap_league_year)
        performance, perf_detail = self._performance(roster, incoming, set(outgoing_ids))
        fit_value, fit_detail = self._fit(team_id, roster, incoming, outgoing)
        contract, contract_detail = self._contract_value(
            incoming, outgoing, salaries, cap_params.salary_cap
        )
        timeline, timeline_detail = self._timeline(strategy, incoming, outgoing)

        # The payroll change is the moved players' own salaries — exact whenever those
        # players are priced. It used to come from `payroll_after - payroll_before`, both
        # of which are `None` unless EVERY rostered player is priced, which is 0 of 30
        # teams under the available contract data. The delta needs the handful of players
        # in the deal, not two whole rosters, and asking for the stronger condition made
        # the term permanently unavailable.
        moved_salaries = [salaries.get(c.player_id) for c in incoming + outgoing]
        payroll_exact = bool(moved_salaries) and all(s is not None for s in moved_salaries)
        payroll_delta: int | None = None
        if payroll_exact:
            payroll_delta = sum(salaries[c.player_id] or 0 for c in incoming) - sum(
                salaries[c.player_id] or 0 for c in outgoing
            )
        assets, assets_detail = self._assets(
            team_id,
            pick_moves,
            payroll_delta,
            payroll_exact,
            len(incoming) - len(outgoing),
        )

        # R3-5: the simulation draws over the SAME rotation the point estimate allocated.
        # An unmodelled player used to receive TEI_SIGMA_DEFAULT = 1.5, expressing the
        # same confidence about a player with no data as about a 36-minute star; they are
        # excluded from the rotation entirely and named in the response instead.
        sigma_by_player = {
            c.player_id: c.tei_sigma
            for c in roster + incoming + outgoing
            if c.tei_sigma is not None
        }
        rotations = perf_detail.pop("_rotations", None)
        # Roster consequences, computed from the SAME allocation the projection used.
        # Re-deriving it here would reintroduce exactly the defect R5.5-1 fixed.
        roster_shape = self._roster_shape(rotations, incoming)
        uncertainty: dict[str, Any]
        if not simulate:
            uncertainty = {
                "n_draws": 0,
                "median": None,
                "p10": None,
                "p90": None,
                "prob_positive": None,
                "skipped": True,
                "unavailable": (
                    "the outcome distribution was not simulated for this evaluation; "
                    "no scored component reads it, and the caller asked for the point "
                    "estimate only"
                ),
                "top_uncertainty_drivers": [],
            }
        elif rotations is None:
            uncertainty = simulate_delta_wins(
                RotationDraw(), RotationDraw(), wins_mapping=self.wins_mapping()
            )
        else:
            before_rot, after_rot = rotations
            uncertainty = simulate_delta_wins(
                before=rotation_draw_from(before_rot.detail, sigma_by_player),
                after=rotation_draw_from(after_rot.detail, sigma_by_player),
                wins_mapping=self.wins_mapping(),
                moved_keys={c.player_id for c in incoming + outgoing},
            )
        unmodeled_in_deal = sorted({c.name for c in incoming + outgoing if c.tei is None})
        if unmodeled_in_deal:
            uncertainty["unmodeled_players_excluded"] = unmodeled_in_deal
        risk, risk_detail = self._risk(roster, incoming, outgoing, legality, team_id)

        components = {
            "performance": performance,
            "fit": fit_value,
            "contract": contract,
            "timeline": timeline,
            "assets": assets,
            "risk": risk,
        }
        utility = composite_utility(components, weights)
        excluded = [k for k, v in components.items() if v is None]
        drivers = component_contributions(components, weights)

        # Players on this side of the deal, or on the roster it is evaluated against,
        # that the impact model has never scored.
        unmodeled_players = sorted({c.name for c in roster + incoming + outgoing if c.tei is None})

        confidence = "high"
        if excluded:
            confidence = "medium" if len(excluded) <= 2 else "low"
        membership_warnings = self._membership_warnings(incoming)
        if membership_warnings and confidence == "high":
            confidence = "medium"
        season_basis = self.season_basis()
        if (
            confidence == "high"
            and fit_value is not None
            and season_basis["roster_differs_from_performance"]
        ):
            # The fit score read needs measured on last season's team. Not "high".
            confidence = "medium"
        if unmodeled_in_deal:
            # A player in the deal itself with no impact estimate is a bigger hole than
            # one sitting at the end of the bench.
            confidence = "low"
        elif unmodeled_players and confidence == "high":
            confidence = "medium"

        return {
            "team_id": team_id,
            "legality": team_legality,
            "has_unmodeled_players": bool(unmodeled_players),
            "unmodeled_players": unmodeled_players,
            "decision_status": "scored" if utility is not None else "insufficient_data",
            "suppression": None
            if utility is not None
            else {
                "reason": "insufficient_data",
                "message": "No component could be scored for this team, so no decision "
                "score is reported.",
            },
            "composite_utility": round(utility, 2) if utility is not None else None,
            "confidence": confidence,
            "components": {
                k: (round(v, 2) if v is not None else None) for k, v in components.items()
            },
            "excluded_components": excluded,
            "drivers": drivers,
            "weights": weights,
            "detail": {
                "performance": perf_detail,
                "roster_shape": roster_shape,
                "fit": fit_detail,
                "contract": contract_detail,
                "timeline": timeline_detail,
                "assets": assets_detail,
                "risk": risk_detail,
            },
            "uncertainty": uncertainty,
            "sensitivity_tornado": tornado(components, weights),
            "incoming": [self._player_summary(c) for c in incoming],
            "outgoing": [self._player_summary(c) for c in outgoing],
            "season_basis": season_basis,
            "membership_warnings": membership_warnings,
            "evaluated_at": datetime.now(UTC).isoformat(),
        }
