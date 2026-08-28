"use client";

/**
 * Pivot — the narrative homepage.
 *
 * Eight acts in the order a front office actually works: observe, diagnose, compare,
 * simulate, recommend, explain — then the door into the product. The scrolling is the
 * argument, not decoration: each act pins while one idea completes, then hands over.
 *
 * Two rules held throughout.
 *
 * 1. **Every number is real.** The acts read the running Pivot API (see `data.ts`) and
 *    show what it returns. Where a capability is not built, the page says so in the same
 *    voice the product uses elsewhere rather than implying it ships today.
 * 2. **The scroll is never taken away.** See `stage.tsx` — sticky sections only, no wheel
 *    capture, no programmatic scrolling, no mandatory snap. Reduced motion turns the whole
 *    thing into a plain vertical page, and so does having JavaScript fail.
 *
 * Removing the experiment is: delete `components/scroll-home/`, restore `app/page.tsx`
 * from `app/command-center/page.tsx`, and put `/` back at the head of PRIMARY in
 * `components/shell.tsx`. Nothing else imports any of this.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { BrandMark } from "@/components/brand";
import { ButtonLink } from "@/components/ui";
import { teamIdentity } from "@/lib/teamIdentity";
import "./scroll-home.css";
import { useAct, useNear, useReveal } from "./stage";
import {
  abbrOf,
  useAcquisitionTargets,
  useFit,
  usePlayerId,
  useTeamProfile,
  useTeams,
  useVocabulary,
  type ProfileEntry,
} from "./data";

/* The three rosters the narrative uses. They are chosen because their measured needs
   genuinely differ — Boston's worst dimension is playmaking, Sacramento's is three-point
   volume, Denver's is point-of-attack defence — which is what makes the fit comparison
   mean something rather than merely look busy. If a roster is missing from the database
   the acts degrade to their structure and show no numbers. */
const CONTEXTS = ["BOS", "SAC", "DEN"] as const;

/* Two players whose skills point opposite ways: a lead guard and a rim-protecting big. */
const SUBJECTS = ["Trae Young", "Rudy Gobert"] as const;

const ACTS = [
  { id: "pivot", label: "Pivot" },
  { id: "observe", label: "Observe" },
  { id: "diagnose", label: "Diagnose" },
  { id: "compare", label: "Compare" },
  { id: "simulate", label: "Simulate" },
  { id: "recommend", label: "Recommend" },
  { id: "explain", label: "Explain" },
  { id: "enter", label: "Enter Pivot" },
];

/* ------------------------------------------------------------------ pieces */

function Eyebrow({ index, children }: { index: string; children: React.ReactNode }) {
  return (
    <div className="eyebrow flex items-center gap-2.5 text-signal">
      <span className="data text-[0.625rem] tracking-[0.2em] text-faint">{index}</span>
      <span aria-hidden className="h-px w-8 bg-signal/45" />
      {children}
    </div>
  );
}

function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  const ref = useReveal<HTMLDivElement>();
  return (
    <div
      ref={ref}
      className={`sh-reveal ${className ?? ""}`}
      style={{ "--reveal-delay": `${delay}ms` } as React.CSSProperties}
    >
      {children}
    </div>
  );
}

/** A measured value drawn as a bar that fills while the act plays. */
function Bar({
  value,
  tone = "signal",
}: {
  /** 0–1. Null renders the track only — an unmeasured value is never drawn as zero. */
  value: number | null;
  tone?: "signal" | "brand" | "legal" | "illegal";
}) {
  const color =
    tone === "brand"
      ? "var(--leather)"
      : tone === "legal"
        ? "var(--legal)"
        : tone === "illegal"
          ? "var(--illegal)"
          : "var(--signal)";
  return (
    <span
      className="sh-bar block h-1.5 w-full overflow-hidden rounded-full bg-panel3"
      style={{ "--v": value ?? 0 } as React.CSSProperties}
      aria-hidden
    >
      <i style={{ background: color }} />
    </span>
  );
}

/** Numbers only ever appear when they were actually returned. */
function Value({ n, suffix = "" }: { n: number | null | undefined; suffix?: string }) {
  if (n === null || n === undefined) {
    return <span className="numeral text-faint">—</span>;
  }
  return (
    <span className="numeral">
      {n}
      {suffix}
    </span>
  );
}

function EntryRow({ entry, tone }: { entry: ProfileEntry; tone: "legal" | "illegal" }) {
  const pctile = entry.percentile === null ? null : Math.round(entry.percentile);
  return (
    <li className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] text-foreground">{entry.label}</span>
        <span className="data text-[11px] text-muted">
          <Value n={pctile} />
          <span className="text-faint">th pct</span>
        </span>
      </div>
      <Bar value={pctile === null ? null : pctile / 100} tone={tone} />
      <p className="text-[11px] leading-snug text-faint">{entry.explanation}</p>
    </li>
  );
}

/* ------------------------------------------------------------------- acts */

function ActHero() {
  const { section, stage } = useAct();
  return (
    <section
      id="pivot"
      ref={section as React.RefObject<HTMLElement>}
      className="sh-act"
      style={{ "--act-vh": 0.6 } as React.CSSProperties}
      aria-labelledby="sh-hero-title"
    >
      <div ref={stage} className="sh-stage court-grid">
        <div className="sh-hero-glow" aria-hidden />
        <div className="sh-inner sh-depth relative">
          <div className="sh-par-slow">
            <Reveal>
              <div className="flex items-center gap-3">
                <BrandMark size={38} />
                <span className="eyebrow text-[0.625rem] text-faint">
                  Basketball decision intelligence
                </span>
              </div>
            </Reveal>

            <Reveal delay={90}>
              <h1
                id="sh-hero-title"
                className="display mt-6 max-w-4xl text-balance text-[clamp(2.5rem,1.2rem+5vw,5.5rem)] leading-[0.92] tracking-tight text-foreground"
              >
                Basketball intelligence for better decisions.
              </h1>
            </Reveal>

            <Reveal delay={180}>
              <p className="mt-6 max-w-lg text-[15px] leading-relaxed text-muted">
                Not a dashboard of statistics. A system that reads a roster, names what it
                is missing, and shows its work.
              </p>
            </Reveal>

            <Reveal delay={260}>
              <div className="mt-9 flex flex-wrap items-center gap-3">
                <ButtonLink href="/command-center" variant="primary">
                  Enter Pivot
                </ButtonLink>
                <a
                  href="#observe"
                  className="eyebrow text-signal underline-offset-4 hover:underline"
                >
                  See how it thinks ↓
                </a>
              </div>
            </Reveal>
          </div>
        </div>

        <div
          aria-hidden
          className="sh-scroll-cue absolute bottom-8 left-1/2 -translate-x-1/2 text-faint"
        >
          <span className="eyebrow text-[0.5625rem]">scroll</span>
        </div>
      </div>
    </section>
  );
}

function ActObserve() {
  const { section, stage } = useAct();
  const { ref, near } = useNear<HTMLDivElement>();
  const { data: teams } = useTeams(near);
  const { data: vocab } = useVocabulary(near);

  return (
    <section
      id="observe"
      ref={section as React.RefObject<HTMLElement>}
      className="sh-act"
      style={{ "--act-vh": 1.3 } as React.CSSProperties}
      aria-labelledby="sh-observe-title"
    >
      <div ref={stage} className="sh-stage">
        <div ref={ref} className="sh-inner sh-depth">
          <Reveal>
            <Eyebrow index="01">Observe</Eyebrow>
          </Reveal>
          <Reveal delay={80}>
            <h2 id="sh-observe-title" className="title-xl mt-4 max-w-2xl text-foreground">
              One view of the league.
            </h2>
          </Reveal>
          <Reveal delay={140}>
            <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-muted">
              Players, rosters, performance and financial context stop being separate tabs.
              Pivot ingests them into one model of the league, and every row keeps the
              source it came from.
            </p>
          </Reveal>

          {/* The real 30 franchises, drawn from the same endpoint the app uses. */}
          <Reveal delay={200}>
            <div className="mt-10 grid grid-cols-6 gap-1.5 sm:grid-cols-10">
              {(teams ?? []).map((team, i) => {
                const identity = teamIdentity(team.abbreviation);
                return (
                  <span
                    key={team.id}
                    title={team.full_name}
                    className="panel flex h-11 items-center justify-center rounded-md text-[10px] font-semibold text-muted"
                    style={
                      {
                        "--edge": identity.bright,
                        opacity: `calc(0.25 + (var(--hold) * 0.75))`,
                        transitionDelay: `${i * 12}ms`,
                      } as React.CSSProperties
                    }
                  >
                    {team.abbreviation}
                  </span>
                );
              })}
              {!teams &&
                Array.from({ length: 30 }).map((_, i) => (
                  <span key={i} className="skeleton h-11 rounded-md" />
                ))}
            </div>
          </Reveal>

          <Reveal delay={260}>
            <dl className="mt-8 flex flex-wrap gap-x-10 gap-y-4 border-t border-hairline pt-5">
              <Stat label="Franchises" value={teams?.length ?? null} />
              <Stat label="Skill dimensions" value={vocab?.skills.length ?? null} />
              <Stat label="Player archetypes" value={vocab?.archetypes.length ?? null} />
              <Stat label="Roster needs" value={vocab?.needs.length ?? null} />
            </dl>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <dt className="eyebrow text-[0.5625rem]">{label}</dt>
      <dd className="mt-1 text-[1.75rem] leading-none">
        <Value n={value} />
      </dd>
    </div>
  );
}

function ActDiagnose() {
  const { section, stage } = useAct();
  const { ref, near } = useNear<HTMLDivElement>();
  const { data: teams } = useTeams(near);
  const { data: vocab } = useVocabulary(near);
  const boston = abbrOf(teams, "BOS");
  const { data: profile } = useTeamProfile(boston?.id, near);

  const measured = vocab?.skills.filter((s) => s.available).length ?? null;
  const total = vocab?.skills.length ?? null;
  const unmeasured = vocab?.skills.filter((s) => !s.available) ?? [];

  return (
    <section
      id="diagnose"
      ref={section as React.RefObject<HTMLElement>}
      className="sh-act"
      style={{ "--act-vh": 1.6 } as React.CSSProperties}
      aria-labelledby="sh-diagnose-title"
    >
      <div ref={stage} className="sh-stage">
        <div ref={ref} className="sh-inner sh-depth">
          <Reveal>
            <Eyebrow index="02">Diagnose</Eyebrow>
          </Reveal>
          <Reveal delay={80}>
            <h2 id="sh-diagnose-title" className="title-xl mt-4 max-w-3xl text-foreground">
              Understand what your roster has — and what it is missing.
            </h2>
          </Reveal>

          <div className="mt-10 grid gap-4 lg:grid-cols-[1fr_1fr_0.9fr]">
            <Reveal delay={140}>
              <div className="panel h-full p-5" style={{ "--edge": "var(--legal)" } as React.CSSProperties}>
                <div className="eyebrow text-legal">Strengths</div>
                <ul className="mt-4 space-y-4">
                  {(profile?.strengths ?? []).slice(0, 3).map((e) => (
                    <EntryRow key={e.key} entry={e} tone="legal" />
                  ))}
                  {!profile && <SkeletonRows />}
                </ul>
              </div>
            </Reveal>

            <Reveal delay={200}>
              <div
                className="panel h-full p-5"
                style={{ "--edge": "var(--illegal)" } as React.CSSProperties}
              >
                <div className="eyebrow text-illegal">Weaknesses</div>
                <ul className="mt-4 space-y-4">
                  {(profile?.weaknesses ?? []).slice(0, 3).map((e) => (
                    <EntryRow key={e.key} entry={e} tone="illegal" />
                  ))}
                  {!profile && <SkeletonRows />}
                </ul>
              </div>
            </Reveal>

            {/* The honesty panel. This is the part most products leave out. */}
            <Reveal delay={260}>
              <div className="panel h-full p-5">
                <div className="eyebrow">What Pivot cannot see</div>
                <p className="mt-3 text-[2rem] leading-none">
                  <Value n={measured} />
                  <span className="text-faint text-[1.25rem]"> / {total ?? "—"}</span>
                </p>
                <p className="mt-1.5 text-[11px] leading-snug text-muted">
                  dimensions are measured today. The rest are named, not quietly dropped:
                </p>
                <ul className="mt-3 space-y-1">
                  {unmeasured.slice(0, 4).map((s) => (
                    <li key={s.key} className="text-[11px] text-faint">
                      · {s.label}
                    </li>
                  ))}
                  {unmeasured.length > 4 && (
                    <li className="text-[11px] text-faint">
                      · and {unmeasured.length - 4} more
                    </li>
                  )}
                </ul>
              </div>
            </Reveal>
          </div>

          <Reveal delay={320}>
            <p className="data mt-5 text-[11px] leading-relaxed text-faint">
              {profile
                ? `${profile.team.full_name} · ${profile.season} · roster ${profile.roster_size} · ${profile.classification_note}`
                : "Measured live from the roster Pivot has ingested."}
            </p>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 3 }).map((_, i) => (
        <li key={i} className="skeleton h-12 rounded-md" />
      ))}
    </>
  );
}

function ActCompare() {
  const { section, stage } = useAct();
  const { ref, near } = useNear<HTMLDivElement>();
  const { data: teams } = useTeams(near);

  const contexts = CONTEXTS.map((abbr) => abbrOf(teams, abbr));
  const { data: playerA } = usePlayerId(SUBJECTS[0], near);
  const { data: playerB } = usePlayerId(SUBJECTS[1], near);

  return (
    <section
      id="compare"
      ref={section as React.RefObject<HTMLElement>}
      className="sh-act"
      style={{ "--act-vh": 2 } as React.CSSProperties}
      aria-labelledby="sh-compare-title"
    >
      <div ref={stage} className="sh-stage">
        <div ref={ref} className="sh-inner sh-depth">
          <Reveal>
            <Eyebrow index="03">Compare</Eyebrow>
          </Reveal>
          <Reveal delay={80}>
            <h2 id="sh-compare-title" className="title-xl mt-4 max-w-2xl text-foreground">
              Talent is not the same as fit.
            </h2>
          </Reveal>
          <Reveal delay={140}>
            <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-muted">
              There is no universal fit score. The same player is worth something different
              to every roster, because every roster is short of something different.
            </p>
          </Reveal>

          <Reveal delay={200}>
            <div className="mt-9 overflow-x-auto scroll-thin">
              <table className="w-full min-w-[560px] border-separate border-spacing-0">
                <thead>
                  <tr>
                    <th scope="col" className="eyebrow pb-3 text-left text-[0.5625rem]">
                      Fit score
                    </th>
                    {contexts.map((team, i) => (
                      <th key={CONTEXTS[i]} scope="col" className="pb-3 pl-4 text-left">
                        <span className="text-[13px] font-semibold text-foreground">
                          {team?.abbreviation ?? CONTEXTS[i]}
                        </span>
                        <NeedCaption teamId={team?.id} enabled={near} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <FitRow name={SUBJECTS[0]} playerId={playerA?.id} contexts={contexts} enabled={near} />
                  <FitRow name={SUBJECTS[1]} playerId={playerB?.id} contexts={contexts} enabled={near} />
                </tbody>
              </table>
            </div>
          </Reveal>

          <Reveal delay={280}>
            <p className="data mt-5 max-w-2xl text-[11px] leading-relaxed text-faint">
              Computed live by Pivot&apos;s fit model against each roster&apos;s measured
              needs. Where a roster has no need clearing the severity threshold, Pivot
              returns no score rather than a flattering one.
            </p>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

/** The team's most severe measured need, shown under its column header. */
function NeedCaption({ teamId, enabled }: { teamId: string | undefined; enabled: boolean }) {
  const { data } = useTeamProfile(teamId, enabled);
  const worst = data?.weaknesses?.[0];
  return (
    <span className="mt-0.5 block text-[10px] leading-tight text-faint">
      {worst ? `needs ${worst.label.toLowerCase()}` : " "}
    </span>
  );
}

function FitRow({
  name,
  playerId,
  contexts,
  enabled,
}: {
  name: string;
  playerId: string | undefined;
  contexts: (ReturnType<typeof abbrOf> | undefined)[];
  enabled: boolean;
}) {
  return (
    <tr>
      <th scope="row" className="border-t border-hairline py-4 pr-4 text-left align-middle">
        <span className="text-[13px] font-medium text-foreground">{name}</span>
      </th>
      {contexts.map((team, i) => (
        <FitCell
          key={CONTEXTS[i]}
          playerId={playerId}
          teamId={team?.id}
          enabled={enabled}
        />
      ))}
    </tr>
  );
}

function FitCell({
  playerId,
  teamId,
  enabled,
}: {
  playerId: string | undefined;
  teamId: string | undefined;
  enabled: boolean;
}) {
  const { data } = useFit(playerId, teamId, enabled);
  const score = data?.available && typeof data.score === "number" ? data.score : null;
  // Warm where the player answers this roster's need, cool where he does not. The colour
  // is a reading of the number Pivot returned, never a second opinion about the player.
  const tone = score === null ? "signal" : score >= 60 ? "brand" : "signal";

  return (
    <td className="border-t border-hairline py-4 pl-4 align-middle">
      <div className="flex items-baseline gap-2">
        <span className="numeral text-[1.5rem] leading-none text-foreground">
          {score === null ? <span className="text-faint">—</span> : score.toFixed(1)}
        </span>
      </div>
      <div className="mt-2 max-w-[150px]">
        <Bar value={score === null ? null : score / 100} tone={tone} />
      </div>
      {data && !data.available && (
        <p className="mt-1.5 text-[10px] leading-tight text-faint">
          {data.already_on_roster ? "already on this roster" : (data.reason ?? "not available")}
        </p>
      )}
    </td>
  );
}

function ActSimulate() {
  const { section, stage } = useAct();
  const { ref, near } = useNear<HTMLDivElement>();
  const { data: teams } = useTeams(near);
  const boston = abbrOf(teams, "BOS");
  const { data: profile } = useTeamProfile(boston?.id, near);
  const { data: player } = usePlayerId(SUBJECTS[0], near);
  const { data: fit } = useFit(player?.id, boston?.id, near);

  const need = profile?.weaknesses?.[0];
  const score = fit?.available && typeof fit.score === "number" ? fit.score : null;

  return (
    <section
      id="simulate"
      ref={section as React.RefObject<HTMLElement>}
      className="sh-act"
      style={{ "--act-vh": 1.8 } as React.CSSProperties}
      aria-labelledby="sh-simulate-title"
    >
      <div ref={stage} className="sh-stage">
        <div ref={ref} className="sh-inner sh-depth">
          <Reveal>
            <Eyebrow index="04">Simulate</Eyebrow>
          </Reveal>
          <Reveal delay={80}>
            <h2 id="sh-simulate-title" className="title-xl mt-4 max-w-2xl text-foreground">
              Test the consequence before you live with it.
            </h2>
          </Reveal>

          {/* Three states, moving horizontally as the act plays. */}
          <div
            className="sh-rail mt-9 items-stretch"
            style={{ "--rail-shift": "calc(var(--hold) * 0px)" } as React.CSSProperties}
          >
            <Reveal delay={140} className="min-w-0 flex-1">
              <Phase tag="Before" tone="illegal">
                <p className="text-[13px] text-foreground">
                  {need ? need.label : "The roster's most severe measured gap"}
                </p>
                <div className="mt-3">
                  <Bar value={need ? need.severity : null} tone="illegal" />
                </div>
                <p className="mt-2 text-[11px] leading-snug text-faint">
                  {need?.explanation ?? "Measured from the ingested roster."}
                </p>
              </Phase>
            </Reveal>

            <Arrow />

            <Reveal delay={200} className="min-w-0 flex-1">
              <Phase tag="Move" tone="signal">
                <p className="text-[13px] text-foreground">
                  Acquire {player?.full_name ?? SUBJECTS[0]}
                </p>
                <p className="mt-3 text-[11px] leading-snug text-muted">
                  A move is a change of membership — who is on which roster. Everything
                  downstream is recomputed from that, not edited by hand.
                </p>
              </Phase>
            </Reveal>

            <Arrow />

            <Reveal delay={260} className="min-w-0 flex-1">
              <Phase tag="After" tone="brand">
                <div className="flex items-baseline gap-2">
                  <span className="numeral text-[2rem] leading-none text-foreground">
                    {score === null ? <span className="text-faint">—</span> : score.toFixed(1)}
                  </span>
                  <span className="eyebrow text-[0.5625rem]">fit</span>
                </div>
                <div className="mt-3">
                  <Bar value={score === null ? null : score / 100} tone="brand" />
                </div>
                <p className="mt-2 text-[11px] leading-snug text-faint">
                  {need
                    ? `Answers the ${need.label.toLowerCase()} gap directly.`
                    : "Scored against this roster's own needs."}
                </p>
              </Phase>
            </Reveal>
          </div>

          <Reveal delay={320}>
            <p className="data mt-6 max-w-2xl text-[11px] leading-relaxed text-faint">
              The fit and the gap above are live values. The full before/after projection —
              payroll, legality under the CBA, and projected wins for both sides — is what
              the{" "}
              <Link href="/trade-evaluator" className="text-signal underline-offset-4 hover:underline">
                Trade Evaluator
              </Link>{" "}
              runs. Pivot will not estimate it here.
            </p>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

function Phase({
  tag,
  tone,
  children,
}: {
  tag: string;
  tone: "illegal" | "signal" | "brand";
  children: React.ReactNode;
}) {
  const edge =
    tone === "illegal" ? "var(--illegal)" : tone === "brand" ? "var(--leather)" : "var(--signal)";
  return (
    <div className="panel h-full p-5" style={{ "--edge": edge } as React.CSSProperties}>
      <div className="eyebrow text-[0.5625rem]" style={{ color: edge }}>
        {tag}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function Arrow() {
  return (
    <span
      aria-hidden
      className="hidden shrink-0 self-center text-faint md:block"
      style={{ opacity: "calc(0.3 + (var(--hold) * 0.7))" }}
    >
      →
    </span>
  );
}

function ActRecommend() {
  const { section, stage } = useAct();
  const { ref, near } = useNear<HTMLDivElement>();
  const { data: teams } = useTeams(near);
  const boston = abbrOf(teams, "BOS");
  const { data: targets } = useAcquisitionTargets(boston?.id, near);

  // A funnel of what SURVIVES each rule, not of what each rule rejected — the response
  // reports the rejections, so the remaining population is derived from them here. Drawing
  // the rejection counts directly (377 "do not improve the need") makes a wide bar out of
  // a step that in fact narrows the field, which reads as the opposite of what happened.
  const s = targets?.search;
  const considered = s?.players_considered ?? null;
  const funnel = s
    ? [
        { label: "Players considered", n: s.players_considered, note: "every rostered player" },
        {
          label: "Skill is measured",
          n: s.players_considered - s.no_skill_measured,
          note: `${s.no_skill_measured} have no measured value for this skill`,
        },
        {
          label: "Improve the need",
          n: s.candidates,
          note: `${s.does_not_improve_the_need} do not clear this roster's own level`,
        },
        ...(targets?.feasibility?.trades_evaluated
          ? [
              {
                label: "Trades evaluated",
                n: targets.feasibility.trades_evaluated,
                note: "each put through the trade evaluator for both sides",
              },
            ]
          : []),
        ...(targets?.targets?.length
          ? [
              {
                label: "Targets returned",
                n: targets.targets.length,
                note: "cleared the generator's conditions on both sides",
              },
            ]
          : []),
      ]
    : [];

  return (
    <section
      id="recommend"
      ref={section as React.RefObject<HTMLElement>}
      className="sh-act"
      style={{ "--act-vh": 1.8 } as React.CSSProperties}
      aria-labelledby="sh-recommend-title"
    >
      <div ref={stage} className="sh-stage">
        <div ref={ref} className="sh-inner sh-depth">
          <Reveal>
            <Eyebrow index="05">Recommend</Eyebrow>
          </Reveal>
          <Reveal delay={80}>
            <h2 id="sh-recommend-title" className="title-xl mt-4 max-w-2xl text-foreground">
              Find the move others don&apos;t see.
            </h2>
          </Reveal>
          <Reveal delay={140}>
            <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-muted">
              Start from the diagnosis, not from a name. Pivot narrows the league to the
              players who improve the thing this roster is actually short of — and states
              both rules it used.
            </p>
          </Reveal>

          <div className="mt-9 grid gap-5 lg:grid-cols-[1.1fr_1fr]">
            <Reveal delay={200}>
              <div className="space-y-2.5">
                {funnel.map((row) => (
                  <div key={row.label}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-[12px] text-muted">{row.label}</span>
                      <span className="numeral text-[15px] text-foreground">{row.n}</span>
                    </div>
                    <span
                      className="sh-funnel-step mt-1.5 block h-2.5 rounded-full bg-signal/70"
                      style={
                        { "--w": considered ? row.n / considered : 1 } as React.CSSProperties
                      }
                      aria-hidden
                    />
                    <p className="mt-1 text-[10.5px] leading-snug text-faint">{row.note}</p>
                  </div>
                ))}
                {!targets &&
                  Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="skeleton h-8 rounded-md" />
                  ))}
              </div>
            </Reveal>

            <Reveal delay={260}>
              <div className="panel h-full p-5" style={{ "--edge": "var(--leather)" } as React.CSSProperties}>
                <div className="eyebrow">The rules, stated</div>
                <dl className="mt-4 space-y-3.5">
                  <div>
                    <dt className="text-[11px] text-faint">Filter</dt>
                    <dd className="mt-0.5 text-[12px] leading-snug text-muted">
                      {targets?.filter_rule ?? "Named in the response, never implied."}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[11px] text-faint">Rank</dt>
                    <dd className="mt-0.5 text-[12px] leading-snug text-muted">
                      {targets?.sort_rule ?? "Named in the response, never implied."}
                    </dd>
                  </div>
                </dl>
                {targets?.targets?.length ? (
                  <div className="mt-5 border-t border-hairline pt-4">
                    <div className="eyebrow text-[0.5625rem]">Top target</div>
                    <p className="mt-1.5 text-[13px] text-foreground">
                      {targets.targets[0].name}{" "}
                      <span className="text-faint">
                        · {targets.targets[0].team.abbreviation}
                      </span>
                    </p>
                  </div>
                ) : null}
              </div>
            </Reveal>
          </div>
        </div>
      </div>
    </section>
  );
}

function ActExplain() {
  const { section, stage } = useAct();
  const { ref, near } = useNear<HTMLDivElement>();
  const { data: vocab } = useVocabulary(near);

  const chain = [
    { k: "Question", d: "Asked in language, by a person who is not writing SQL." },
    { k: "Pivot tools", d: "A read-only surface: vocabulary, player profile, team profile, fit." },
    { k: "Evidence", d: "Real rows and computed values, each with its method and confidence." },
    { k: "Explanation", d: "Assembled from that evidence — and quoting it." },
  ];

  return (
    <section
      id="explain"
      ref={section as React.RefObject<HTMLElement>}
      className="sh-act"
      style={{ "--act-vh": 1.6 } as React.CSSProperties}
      aria-labelledby="sh-explain-title"
    >
      <div ref={stage} className="sh-stage">
        <div ref={ref} className="sh-inner sh-depth">
          <Reveal>
            <Eyebrow index="06">Explain</Eyebrow>
          </Reveal>
          <Reveal delay={80}>
            <h2 id="sh-explain-title" className="title-xl mt-4 max-w-3xl text-foreground">
              A language layer over the model — never in place of it.
            </h2>
          </Reveal>

          <Reveal delay={140}>
            <div className="mt-8 grid gap-2.5 md:grid-cols-4">
              {chain.map((step, i) => (
                <div
                  key={step.k}
                  className="panel p-4"
                  style={
                    {
                      "--edge": i === 1 ? "var(--signal)" : "var(--hairline)",
                      opacity: `calc(0.3 + (var(--hold) * 0.7))`,
                      transitionDelay: `${i * 70}ms`,
                    } as React.CSSProperties
                  }
                >
                  <div className="eyebrow text-[0.5625rem] text-signal">
                    {String(i + 1).padStart(2, "0")}
                  </div>
                  <p className="title-md mt-2 text-foreground">{step.k}</p>
                  <p className="mt-1.5 text-[11px] leading-snug text-muted">{step.d}</p>
                </div>
              ))}
            </div>
          </Reveal>

          <Reveal delay={220}>
            <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_1fr]">
              <div className="panel p-5">
                <div className="eyebrow">Evidence is ranked, not asserted</div>
                <ul className="mt-3.5 space-y-2.5">
                  {(vocab?.evidence_ladder ?? []).map((e) => (
                    <li key={e.key} className="flex gap-3">
                      <span className="data w-20 shrink-0 text-[11px] text-signal">{e.label}</span>
                      <span className="text-[11px] leading-snug text-muted">{e.definition}</span>
                    </li>
                  ))}
                  {!vocab && <SkeletonRows />}
                </ul>
              </div>

              {/* Said plainly, in the same voice the product uses elsewhere. */}
              <div className="rounded-xl border border-dashed border-line p-5">
                <div className="eyebrow text-conditional">Product direction — not shipped</div>
                <p className="mt-3 text-[13px] leading-relaxed text-muted">
                  Pivot AI is not built. The architecture above is the commitment being made
                  about it: the model answers from tool results, and a claim it cannot trace
                  to one is a bug, not a feature.
                </p>
                <p className="mt-3 text-[12px] leading-relaxed text-faint">
                  Pivot won&apos;t ship a model it can&apos;t validate, or a page that
                  promises one.
                </p>
              </div>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

function ActEnter() {
  const { section, stage } = useAct();
  return (
    <section
      id="enter"
      ref={section as React.RefObject<HTMLElement>}
      className="sh-act"
      style={{ "--act-vh": 0.5 } as React.CSSProperties}
      aria-labelledby="sh-enter-title"
    >
      <div ref={stage} className="sh-stage court-grid">
        <div className="sh-hero-glow" aria-hidden />
        <div className="sh-inner relative text-center">
          <Reveal>
            <div className="flex justify-center">
              <BrandMark size={44} />
            </div>
          </Reveal>
          <Reveal delay={80}>
            <h2
              id="sh-enter-title"
              className="display mt-6 text-[clamp(2.25rem,1.2rem+4vw,4.25rem)] leading-[0.95] text-foreground"
            >
              Enter Pivot.
            </h2>
          </Reveal>
          <Reveal delay={140}>
            <p className="mx-auto mt-4 max-w-md text-[15px] leading-relaxed text-muted">
              The narrative ends here. What follows is the working environment — real
              rosters, real cap rules, and every number traceable to how it was produced.
            </p>
          </Reveal>
          <Reveal delay={200}>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <ButtonLink href="/command-center" variant="primary">
                Open the Command Center
              </ButtonLink>
              <ButtonLink href="/methodology" variant="secondary">
                Read the methodology
              </ButtonLink>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------- shell */

/**
 * A quiet act indicator. Plain anchors, so it works with a keyboard, with the browser's
 * own back/forward, and before any JavaScript has run.
 *
 * The active mark is tracked with one IntersectionObserver rather than from the scroll
 * loop: it fires only when an act crosses the middle of the viewport — a handful of times
 * over the whole page — instead of setting React state on every frame.
 */
function ProgressRail() {
  const [active, setActive] = useState(ACTS[0].id);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting && entry.target.id) setActive(entry.target.id);
        }
      },
      // A thin band across the middle of the viewport: whichever act is under it is the
      // one being read.
      { rootMargin: "-45% 0px -45% 0px" },
    );
    for (const act of ACTS) {
      const el = document.getElementById(act.id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, []);

  return (
    <nav className="sh-progress" aria-label="Sections">
      <ul>
        {ACTS.map((act) => (
          <li key={act.id}>
            <a href={`#${act.id}`} aria-current={active === act.id ? "true" : undefined} title={act.label}>
              <span className="sr-only">{act.label}</span>
              <i aria-hidden />
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function ScrollHome() {
  return (
    <div className="sh">
      <ProgressRail />
      <ActHero />
      <ActObserve />
      <ActDiagnose />
      <ActCompare />
      <ActSimulate />
      <ActRecommend />
      <ActExplain />
      <ActEnter />
    </div>
  );
}
