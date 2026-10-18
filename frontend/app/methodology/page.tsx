import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui";
import {
  DocFormula,
  DocPoints,
  DocSection,
  DocTech,
  DocToc,
  type DocTocItem,
} from "@/components/reference/doc";
import { ImpactReferenceLive } from "@/components/reference/impact-reference";

export const metadata: Metadata = { title: "Methodology" };

/*
 * Anchors other pages link to — #utility, #tei, #needs, #skills, #archetypes, #weights,
 * #rules — are section ids here and keep `scroll-mt-24`. #percentile is the League
 * Impact Percentile, linked from every impact readout through #tei.
 *
 * Laid out for reading: a sticky contents rail that marks the section in view, then one
 * column of prose at a ~68-character measure with serif section heads. Formulas and
 * validation numbers sit in quiet cards a reader opens on purpose; the one live
 * instrument (the league impact reference) is drawn from the API.
 */
const TOC: DocTocItem[] = [
  { id: "scope", number: "01", label: "What the model can't know" },
  { id: "utility", number: "02", label: "The decision score" },
  { id: "tei", number: "03", label: "Player impact (TEI)" },
  { id: "percentile", number: "03.1", label: "League impact percentile", sub: true },
  { id: "wins", number: "04", label: "Projected wins" },
  { id: "needs", number: "05", label: "Needs and fit" },
  { id: "skills", number: "06", label: "Player skills" },
  { id: "archetypes", number: "07", label: "Archetypes" },
  { id: "weights", number: "08", label: "Strategy weights" },
  { id: "rules", number: "09", label: "Trade rules check" },
  { id: "sources", number: "10", label: "Data sources" },
];

/** Section numbers come from the contents list, so the two cannot drift apart. The list
 *  once numbered the percentile subsection as its own section and every later entry
 *  was one ahead of the section it linked to. */
const NUM: Record<string, string> = Object.fromEntries(TOC.map((item) => [item.id, item.number]));

export default function MethodologyPage() {
  return (
    <div>
      <PageHeader
        eyebrow="Documentation"
        title="How Pivot thinks"
        lede={
          <>
            Plain-language explanations first; the exact formulas and validation numbers are in the
            expandable technical sections and in{" "}
            <code className="text-[13px] text-foreground">docs/methodology.md</code>. Every number in
            the product traces to one of the calculations on this page.
          </>
        }
      />

      <div className="grid grid-cols-1 gap-x-14 gap-y-10 lg:grid-cols-[14.5rem_minmax(0,1fr)] xl:gap-x-20">
        <aside className="lg:sticky lg:top-[calc(var(--nav-h)+2rem)] lg:max-h-[calc(100dvh-var(--nav-h)-4rem)] lg:self-start lg:overflow-y-auto">
          <DocToc items={TOC} />
        </aside>

        {/* The reading column: ~68 characters of the body face per line. */}
        <article className="min-w-0 max-w-[68ch] space-y-12 text-[15.5px]">
          <DocSection id="scope" number={NUM.scope} title="What the model evaluates — and what it can't know">
            <p>
              Pivot evaluates a trade the way a front office frames it: does the deal make the team
              better on the court, does it fit the roster, is the money sensible, does it match the
              competitive window, what flexibility does it cost, and what&apos;s the downside? It does{" "}
              <em>not</em>{" "}know locker-room chemistry, medical files, private negotiations, or the
              future — which is why every projection ships with an uncertainty range and why missing
              data becomes an explicit &quot;unavailable&quot; instead of a guess.
            </p>
          </DocSection>

          <DocSection id="utility" number={NUM.utility} title="The decision score">
            <p>
              Each team in a deal gets a <strong>decision score out of 100</strong>{" "}(50 = neutral).
              It&apos;s a weighted blend of six components — on-court impact, roster fit, contract
              value, competitive window, flexibility, and downside risk — where <em>you</em>{" "}control
              the weights through your strategy. It is deliberately not a single opaque
              &quot;winner&quot; score: every component and its raw calculation is shown alongside.
            </p>
            <p>
              <strong>When data is missing</strong>{" "}(for example contract value with no contract
              import), that component is dropped and the remaining weights are re-scaled. The score
              never contains an invented number — and the UI tells you what was excluded.
            </p>
            <DocTech>
              <DocFormula>
                {`U = w_P·Performance + w_F·Fit + w_C·Contract + w_T·Window + w_A·Flexibility + w_R·Risk
Σ w_k = 1   (user weights, renormalized over available components)`}
              </DocFormula>
              <p>
                Components are normalized to 0–100. Verdict labels are monotone in the score: ≥58
                Clear win · 48–58 Roughly neutral · 40–48 Net negative · &lt;40 Clear loss · low
                confidence ⇒ &quot;Cannot fully evaluate&quot;. A deal that fails a verified CBA rule
                receives no score at all — &quot;No decision score&quot;, with the failing rules shown
                instead.
              </p>
              <p>
                Downside protection (the risk component) is availability exposure: the minutes-weighted games-played
                availability of the arriving players minus the departing ones, mapped onto 0–100, so
                higher means the deal sheds games-missed exposure. The simulation&apos;s chance that
                a deal helps is reported beside it, never scored a second time.
              </p>
            </DocTech>
          </DocSection>

          <DocSection id="tei" number={NUM.tei} title="Estimated player impact (TEI)">
            <p>
              Every scored player carries an <strong>estimated impact</strong>{" "}number on
              Pivot&apos;s own index scale. Its full name is Pivot Estimated Impact (TEI). It is
              deliberately <em>not</em>{" "}labelled &ldquo;points per 100 possessions&rdquo;: the index
              is a weighted z-score composite of box-score rates, scaled by 2.5, and the fitted
              conversion from a team&apos;s minutes-weighted index to net-rating points is{" "}
              <strong>≈15</strong>, not 1.
            </p>
            <p>
              It is built from three seasons of real box-score data, and it is <em>not</em>{" "}RAPTOR,
              EPM, LEBRON or BPM. Because it only sees box scores, defense is under-measured — treat
              small differences as noise; the uncertainty band on player pages is there for a
              reason.
            </p>
            <DocTech>
              <p>Recency-weighted features (λ=0.7, minutes-weighted, seasons 2023-24 → 2025-26):</p>
              <DocFormula>{`X̄ᵢ = Σₛ λ^(s−1)·mᵢ,ₛ·Xᵢ,ₛ / Σₛ λ^(s−1)·mᵢ,ₛ`}</DocFormula>
              <p>
                A transparent weighted z-score index with documented fixed weights. A ridge
                challenger was served until R3-1 on a held-out player-level MAE of 0.637 against the
                index&apos;s 0.645 — a comparison on a next-season proxy, which is not the question
                the product asks. Measured at team level, where it was actually used, the ridge
                explained <strong>R² = 0.004</strong>{" "}of net rating against the index&apos;s{" "}
                <strong>0.751</strong>. It is retired.
              </p>
              <p>Uncertainty bands are per player, not constant:</p>
              <DocFormula>σ² = 0.0326 + 240.9 / total minutes</DocFormula>
              <p>
                estimated from 921 same-player consecutive-season pairs. σ runs 0.72 at 500 minutes
                to 0.36 at 2,500, replacing a single 2.462 taken from the retired model&apos;s
                residual spread. Most bands get narrower — which reads as overconfidence and is the
                opposite — while the thinnest-evidence players&apos; bands get wider. Scores ×2.5 to
                index points; elite ≈ +5. Model card: docs/model-card-player-impact.md.
              </p>
            </DocTech>

            {/* ------------------------------------- League Impact Percentile */}
            <section
              id="percentile"
              aria-labelledby="percentile-heading"
              className="mt-10 scroll-mt-24 space-y-4 border-t border-hairline pt-8"
            >
              <div>
                <div className="eyebrow numeral tracking-[0.06em] text-signal">{NUM.percentile}</div>
                <h3
                  id="percentile-heading"
                  className="mt-2 text-balance font-serif text-[clamp(1.4rem,1.15rem+0.7vw,1.75rem)] leading-[1.1] text-foreground"
                >
                  The league impact percentile
                </h3>
              </div>
              <p>
                A raw index such as <span className="numeral text-foreground">+1.8</span>{" "}only
                means something next to the other indices it was scored with. So wherever Pivot
                shows a player&apos;s current impact — rosters, player pages, trade evaluations,
                acquisition targets — it leads with his <strong>league impact percentile</strong>:
                where his index sits among every player the model measured. The raw index is
                shown in each readout and roster row, beside the percentile on the trade report,
                and in the tooltip of every compact percentile.
              </p>
              <DocPoints
                items={[
                  {
                    term: "The population",
                    body: (
                      <>
                        Every player scored for the current season under the active model: at least
                        200 minutes across the model&apos;s window, and on a current roster or listed
                        as active. One shared list, served by the backend.
                      </>
                    ),
                  },
                  {
                    term: "Mid-rank percentile",
                    body: (
                      <>
                        The share of scored players with a lower index, with players on exactly the
                        same index counted as half. It is never 0 or 100, so it is shown between 1st
                        and 99th.
                      </>
                    ),
                  },
                  {
                    term: "Competition rank",
                    body: (
                      <>
                        1 plus the number of scored players with a higher index. Players on the same
                        index share a rank, shown as &ldquo;T-#12&rdquo;.
                      </>
                    ),
                  },
                  {
                    term: "Likely range",
                    body: (
                      <>
                        Each estimate&apos;s 10–90% band, with both ends placed in the same
                        population. A wide range means the box score cannot pin the position down —
                        neighbouring ranks usually overlap inside it.
                      </>
                    ),
                  },
                ]}
              />
              <p>
                <strong>A percentile is a position, not a rating.</strong>{" "}&ldquo;90th&rdquo; says
                the index is higher than about nine in ten measured players; it is not a calibrated
                0–100 grade, it does not make a player twice as good as one at the 45th, and equal
                gaps in percentile are not equal gaps in impact.
              </p>
              <p>
                <strong>Unscored players are never ranked.</strong>{" "}A player the model did not
                score — usually one below the minutes threshold — has no estimate, so he is shown as &ldquo;unscored&rdquo; — never placed at
                zero, at the median or at the bottom — and he is not part of the population anyone
                else is ranked against.
              </p>
              <p>
                One place shows the raw index instead: trade precedents, whose index is a
                single-season construction for the season the historical trade was made and a
                different quantity from this population. Saved-deal reports are placed like
                everything else, because a saved deal is re-scored with the active model each time
                it is opened.
              </p>
              <ImpactReferenceLive />
            </section>
          </DocSection>

          <DocSection id="wins" number={NUM.wins} title="Projected wins and the rotation">
            <p>
              A trade changes who plays, not just who&apos;s on the roster. Pivot reallocates the 240
              minutes in an NBA game across the post-trade roster (proportional to established roles,
              capped per player), discounts by each player&apos;s historical availability, and
              charges any minutes the roster cannot fill to a replacement-level player.
            </p>
            <p>
              The team-quality change converts to net-rating points through a coefficient fitted
              change-on-change on 60 team transitions (≈15, t = 9.8), then to wins through a
              conversion fit on real team-seasons. The same 2,000-draw simulation runs over that same
              reallocation, so the range you see and the number above it are one quantity rather than
              two — which is why a projected wins change is always shown with its 10th–90th
              percentile band, over a full 82-game season.
            </p>
            <DocTech>
              <DocFormula>
                {`ΔW = slope · ΔNetRating · (games/82)
slope 2.235 wins/point · fit on 90 team-seasons · R²=0.953 · σ=2.9 wins`}
              </DocFormula>
              {/* Matches backend/app/analytics/uncertainty.py. The earlier wording listed
                  "minutes (±12%)" and "the conversion slope (±15%)": minutes are not drawn
                  at all, and the slope is drawn with its fitted standard error (~2%). */}
              <p>
                2,000 Monte Carlo draws over each player&apos;s impact (normal, with σ taken from his
                own 10–90% band), his availability (Beta around his historical games-played rate),
                and the wins-per-point slope (its fitted standard error). Rotation minutes are held
                at the allocation above, and the ≈15 net-rating coefficient is held fixed, so the
                band leaves out that conversion&apos;s own uncertainty. Reported: median,
                10th–90th percentile, P(deal helps). Availability is historical games played — not
                a medical prediction.
              </p>
            </DocTech>
          </DocSection>

          <DocSection id="needs" number={NUM.needs} title="Roster needs, strengths and fit">
            <p>
              Team needs (&quot;3PT volume&quot;, &quot;rim protection&quot;…) come from transparent
              percentile rules over real league stats — a team in the bottom third for three-point
              attempts has a shooting need; no AI, no scouting opinions. Trade fit then asks: do the
              incoming players address those needs without duplicating what the roster already does
              well? Proxies are labeled (blocks ≈ rim protection, steals ≈ point-of-attack pressure).
            </p>
            <p>
              Fit is reported on a 0–100 scale where <strong>50 changes nothing</strong>. It measures
              direction against this roster&apos;s needs, not the size of the improvement, and it is
              not a talent rating. When no need is strong enough, or a player has no measured skills,
              no score is returned and the reason is shown.
            </p>
            <DocTech>
              <DocFormula>F = Σₖ nₖ·Δsₖ − γ·Σₖ max(0, rₖ)   (γ = 0.35)</DocFormula>
              <p>
                n = need severity (0–1 from percentile shortfall), Δs = minutes-weighted change in
                skill percentile (incoming − outgoing), r = redundancy above the roster&apos;s 70th
                percentile skills.
              </p>
            </DocTech>
          </DocSection>

          <DocSection id="skills" number={NUM.skills} title="Player skills — the nine Pivot measures, and the thirteen it does not">
            <p>
              Pivot describes a player by what he does, not by where he stands. Nine capabilities are
              measured from ingested box-score data as <strong>league percentiles</strong>{" "}— shooting
              volume and accuracy, shot creation, ball security, team defense, rim protection,
              rebounding, positional size and scoring. A skill whose inputs are missing is{" "}
              <em>omitted</em>, never filled with a league median: 0.5 is not a measurement.
            </p>
            <p>
              Pivot also declares <strong>thirteen dimensions it cannot see</strong>{" "}— switchability,
              screen navigation, rim pressure, off-ball gravity and the rest — and shows them on every
              player page with the reason. They need player-tracking or matchup data, and no amount of
              modelling substitutes for it. Naming them is the point: a reader should learn that
              Pivot cannot see switchability, not conclude that switchability does not matter.
            </p>
            <DocTech>
              <p>
                One dimension was <strong>built and then withdrawn</strong>. A point-of-attack
                defense composite scored <em>worse</em>{" "}than the steals proxy it was meant to replace,
                on its own pre-registered class (0.630 against 0.611), because gambling for steals is
                what a box score records and staying in front of a ball handler is not. The team-side
                need is still measured and still shown; no player skill claims to fix it.
              </p>
              <p>
                Every value carries its evidence class —{" "}
                <code className="text-[13px] text-foreground">observed</code>{" "}(a provider
                reported it), <code className="text-[13px] text-foreground">derived</code>{" "}
                (arithmetic over observations), or{" "}
                <code className="text-[13px] text-foreground">inferred</code>{" "}(a basketball
                attribute asserted from them) — because an inference can be wrong in a way a
                derivation cannot.
              </p>
            </DocTech>
          </DocSection>

          <DocSection id="archetypes" number={NUM.archetypes} title="Archetypes — what kind of player, not what position">
            <p>
              &quot;Power forward&quot; describes where a player once stood; &quot;stretch big&quot;
              describes what he does to a defense. Pivot places every player into one of{" "}
              <strong>fourteen functional archetypes</strong>{" "}using a deterministic rule chain over
              league percentile cut points — nothing is fitted, nothing is random, and repeated runs
              are byte-identical.
            </p>
            <p>
              Size gates first, deliberately: a creation-first ordering — the intuitive one —
              labelled Victor Wembanyama a secondary creator.
            </p>
            <DocTech>
              <p>
                The chain replaced k-means clustering in R4-3, which was not merely imprecise but
                degenerate: only 5 of its 10 label branches ever fired, 34.3 % of players carried a
                numeric suffix because clusters collided, and{" "}
                <strong>dropping a random 10 % of players rewrote 65.7 % of the survivors&apos; labels</strong>
                . The replacement moves 1.78 % on the identical protocol, and all fourteen roles fire.
              </p>
              <p>
                An archetype is an <em>inference</em>, and Pivot never calls it validated: no
                ground-truth archetype set exists to check the labels against. A player holds exactly
                one today; the data model already allows several with weights, which is where a
                future engine will put a second claim.
              </p>
            </DocTech>
          </DocSection>

          <DocSection id="weights" number={NUM.weights} title="Strategy weights and sensitivity">
            <p>
              Your strategy (contend, rebuild, re-tool…) sets how much each component matters.
              Because weights are a judgment call, Pivot stress-tests every comparison: it re-runs the
              ranking under hundreds of nearby weightings and reports how often each deal finishes
              first. A deal that only wins under one exact weighting isn&apos;t a robust
              recommendation — and the product says so.
            </p>
            <DocTech>
              <p>
                500 Dirichlet samples centered on your weights (concentration 50) → first-place
                share, rank volatility, median rank; plus one-at-a-time ±50% tornado bars per
                component. The Strategy Lab&apos;s live sliders re-blend the <em>stored</em>{" "}component
                scores client-side with the same renormalization rule — exploratory, clearly labeled.
              </p>
            </DocTech>
          </DocSection>

          <DocSection id="rules" number={NUM.rules} title="Trade rules check">
            <p>
              The rules engine verifies a documented subset of the 2023 CBA: salary matching bands,
              first/second-apron limits, aggregation restrictions, roster limits, recently-signed
              windows, no-trade clauses and two-way exclusions.
            </p>
            <DocPoints
              items={[
                { term: <><span aria-hidden className="text-legal">✓ </span>Passes</>, body: "Every rule that can be verified with current data passed." },
                { term: <><span aria-hidden className="text-illegal">✕ </span>Fails</>, body: "At least one verified rule fails. The deal gets no decision score." },
                {
                  term: <><span aria-hidden className="text-conditional">~ </span>Incomplete (data missing)</>,
                  body: "Everything verifiable passed, but a required check — usually salary matching — could not run.",
                },
                { term: <><span aria-hidden className="text-unavail">— </span>Not checked</>, body: "Not enough data to run meaningful rule checks." },
              ]}
            />
            <p>
              A deal is never called legal from partial data — without imported contracts, salary
              rules report &quot;unavailable&quot; and the best possible outcome is
              &quot;incomplete&quot;.
            </p>
            <DocTech>
              <DocFormula>
                {`below 1st apron:    max_in = 200%·out + $250K  (≤ $8.85M)
                    max_in = out + $9.10M      (≤ $35.4M)
                    max_in = 125%·out + $250K
at/above 1st apron: max_in = out + $250K
2nd apron:          no aggregation`}
              </DocFormula>
              <p>
                2025-26 anchors scale with the cap per the CBA; verified cap figures for 2025-26 and
                2026-27 ship as sourced YAML. Full rule-by-rule coverage, sources and tests:
                docs/cba-rule-coverage.md. Not covered (and never faked): sign-and-trades, trade
                exceptions, cash, base-year compensation, hard-cap triggers.
              </p>
            </DocTech>
          </DocSection>

          <DocSection id="sources" number={NUM.sources} title="Data sources & honesty">
            <p>
              Basketball data comes from NBA.com via the open-source{" "}
              <code className="text-[13px] text-foreground">nba_api</code>{" "}client; the
              2025-26 totals table is a user-imported CSV keyed by official player IDs; historical
              bio data comes from a Kaggle research database (fills gaps only, never overwrites);
              contracts come from a user-downloaded Basketball-Reference snapshot; photos and logos
              are local assets matched to identities with recorded confidence.
            </p>
            <p>
              Every screen shows its source and freshness, and{" "}
              <Link
                className="text-signal underline decoration-signal/40 underline-offset-4 hover:decoration-signal"
                href="/data-health"
              >
                Data Health
              </Link>{" "}
              shows exactly what&apos;s missing. Details: docs/data-sources.md and
              docs/identity-resolution.md.
            </p>
          </DocSection>
        </article>
      </div>
    </div>
  );
}
