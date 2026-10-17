import type { Metadata } from "next";
import Link from "next/link";
import { IconArrowRight } from "@/components/icons";
import { DOC_HEADING, DOC_PROSE } from "@/components/reference/doc";
import { Badge, PageHeader } from "@/components/ui";

export const metadata: Metadata = { title: "About — Pivot" };

// R5 and R6 shipped three surfaces an earlier version of this page never mentioned —
// comparable trades, need-driven acquisition and the decision memo. Each one now sits at
// the stage of the decision it actually serves, rather than at the end of a flat list.
const WORKFLOW: { stage: string; blurb: string; surfaces: { name: string; href: string }[] }[] = [
  {
    stage: "Observe",
    blurb:
      "what the roster is right now: players with photos, imported 2025-26 stat lines with per-game derivations, and payroll by season from imported contract snapshots — league percentiles taken only over players whose sample can support one.",
    surfaces: [
      { name: "Team Outlook", href: "/team-outlook" },
      { name: "Player Explorer", href: "/player-explorer" },
      { name: "Salary-Cap Center", href: "/salary-cap-center" },
    ],
  },
  {
    stage: "Diagnose",
    blurb:
      "what that roster is short of and what it already does well — model-derived strengths and needs, competitive window, and payroll status that stays fully empty until contracts are imported.",
    surfaces: [{ name: "Team Outlook", href: "/team-outlook" }],
  },
  {
    stage: "Compare",
    blurb:
      "the completed trades a proposal most resembles, retrieved from ten seasons of Basketball-Reference transaction pages — evidence rather than model output — alongside multi-player comparison.",
    surfaces: [
      { name: "Comparable trades", href: "/trade-evaluator" },
      { name: "Player Explorer", href: "/player-explorer" },
    ],
  },
  {
    stage: "Simulate",
    blurb:
      "2–3-team construction with live backend rules checks, fan verdicts, and advanced analytics behind progressive disclosure.",
    surfaces: [{ name: "Trade Evaluator", href: "/trade-evaluator" }],
  },
  {
    stage: "Recommend",
    blurb:
      "saved deals side by side with live priority re-weighting, Pareto frontier and rank-stability analysis; and acquisition candidates that start from the diagnosed shortfall, keep only players who actually improve the roster, and rank by projected wins — each one run through the trade evaluator before it is shown.",
    surfaces: [
      { name: "Strategy Lab", href: "/strategy-lab" },
      { name: "Team Outlook", href: "/team-outlook" },
    ],
  },
  {
    stage: "Explain",
    blurb:
      "the evaluation as a reviewable artifact, including an explicit list of what is not known about the deal. Exportable from a saved trade.",
    surfaces: [{ name: "Decision memo", href: "/trade-evaluator" }],
  },
];

const HONESTY = [
  "No synthetic NBA data in production paths — fixtures live only in test directories.",
  "Missing data is an explicit state: a trade is never called legal from partial checks, absent salaries are never estimated, unmatched images are kept for review instead of guessed.",
  "Every provider-derived record stores provider, endpoint/file, timestamps and run id; sources that disagree are logged, not silently overwritten.",
  "Every screen shows source and freshness; Data Health reports what's missing in plain language.",
];

/**
 * The system, as data: the request path from this interface to the server, then what the
 * server runs and what it reads from. It is drawn as a short flow and two lists rather
 * than as a ruled text tree — the tree drew its trunk in box characters (and later in
 * CSS) to say only "these hang from the server", which a heading says plainly.
 */
const ARCHITECTURE: {
  client: string;
  path: string;
  server: string;
  services: string[];
  integrations: string[];
} = {
  client: "Next.js 16 (this UI)",
  path: "/api/v1",
  server: "FastAPI",
  services: [
    "CBA rules engine (four-state honesty standard)",
    "Evaluation (6 components + Monte Carlo + sensitivity)",
    "Analytics (TEI, archetypes, needs, projections, rotation allocator, pick valuation)",
    "Comparable-trade retrieval (10 seasons, side-level)",
    "SQLAlchemy + Alembic (35 tables, full provenance)",
    "Media asset manifest (photos/logos by NBA id)",
  ],
  integrations: [
    "nba_api ← NBA.com (rate-limited, circuit-broken)",
    "user CSV totals import (PLAYER_ID-keyed)",
    "Kaggle basketball DB (NULL-fill enrichment)",
    "Basketball-Reference contracts snapshot parser",
    "Basketball-Reference transaction pages (trades)",
    "RealGM future-drafts snapshot (pick ownership)",
  ],
};

function SectionHead({ eyebrow, title, id }: { eyebrow: string; title: string; id: string }) {
  return (
    <div>
      <div className="eyebrow">{eyebrow}</div>
      <h2 id={id} className={`${DOC_HEADING} mt-2.5`}>
        {title}
      </h2>
    </div>
  );
}

function Node({ role, name }: { role: string; name: string }) {
  return (
    <div className="sheet min-w-0 px-4 py-3 sm:min-w-[11rem]">
      <div className="eyebrow">{role}</div>
      <div className="mt-1 text-[14.5px] font-medium text-foreground">{name}</div>
    </div>
  );
}

function SystemList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="min-w-0 bg-panel px-5 py-5">
      <h3 className="eyebrow flex items-baseline gap-2">
        {title}
        <span className="numeral font-medium tracking-normal text-faint">{items.length}</span>
      </h3>
      <ul className="mt-3.5 space-y-2.5">
        {items.map((item) => (
          <li key={item} className="flex gap-2.5 text-[13.5px] leading-snug text-muted">
            <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-faint" />
            <span className="min-w-0">{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Architecture() {
  const { client, path, server, services, integrations } = ARCHITECTURE;
  return (
    <div className="panel overflow-hidden">
      {/* The request path: this interface calls the server through one versioned API. */}
      <div className="flex flex-col gap-0 px-5 py-5 sm:flex-row sm:items-center">
        <Node role="Interface" name={client} />
        <div className="flex items-center gap-3 py-1 pl-4 sm:flex-1 sm:flex-col sm:items-stretch sm:gap-1 sm:px-3 sm:py-0">
          <code className="order-2 text-[12px] text-muted sm:order-1 sm:text-center">{path}</code>
          <span aria-hidden className="order-1 flex flex-col items-center text-faint sm:order-2 sm:flex-row">
            <span className="h-5 w-px bg-line sm:h-px sm:w-auto sm:flex-1" />
            <IconArrowRight size={14} className="-mt-2 rotate-90 sm:-ml-2.5 sm:mt-0 sm:rotate-0" />
          </span>
        </div>
        <Node role="Server" name={server} />
      </div>
      <div className="grid gap-px border-t border-hairline bg-hairline md:grid-cols-2">
        <SystemList title="What the server runs" items={services} />
        <SystemList title="What it reads from" items={integrations} />
      </div>
      <p className="border-t border-hairline px-5 py-3 text-[12px] text-faint">
        In the repository:{" "}
        <code className="text-[12px] text-muted">docs/architecture.md</code>
      </p>
    </div>
  );
}

export default function AboutPage() {
  return (
    <div>
      <PageHeader
        eyebrow="The project"
        title="About Pivot"
        lede="A basketball decision-intelligence system built as a portfolio project — structured decision-making under real constraints, with data honesty as the core design value."
      />

      <div className="grid grid-cols-1 gap-x-14 gap-y-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,21rem)] xl:gap-x-20">
        <div className="min-w-0 max-w-[46rem] space-y-14">
          {/* --------------------------------------------------- the problem */}
          <section aria-labelledby="about-problem" className="space-y-6">
            <SectionHead eyebrow="The problem it structures" title="One question, six pulls" id="about-problem" />
            <blockquote className="border-l-2 border-signal/70 pl-5">
              <p className="font-serif text-[clamp(1.9rem,1.4rem+1.5vw,2.75rem)] italic leading-[1.05] text-foreground">
                &ldquo;Should we make this trade?&rdquo;
              </p>
            </blockquote>
            <p className={DOC_PROSE}>
              It is an ambiguous, multi-objective question: on-court value, cap law, roster fit,
              timelines, risk and optionality all pull in different directions, and the right answer
              depends on strategy. Pivot makes the frame explicit — pick a team and a strategy, build
              against real rosters, get an honest rules check and an explainable multi-component
              evaluation with uncertainty, then stress-test the conclusion. It&apos;s a
              decision-support system for exploration, not a replacement for a front office.
            </p>
          </section>

          {/* --------------------------------------------------- the workflow */}
          <section aria-labelledby="about-workflow" className="space-y-6">
            <SectionHead eyebrow="One system, six stages" title="The decision workflow" id="about-workflow" />
            <p className={DOC_PROSE}>
              Pivot is one system rather than a suite of separate tools. The same evaluation runs
              underneath every screen, and the screens are stages of a single decision:
            </p>
            <ol>
              {WORKFLOW.map((step, i) => (
                <li key={step.stage} className="relative flex gap-5 pb-7">
                  {/* The rail to the next stage, in the order a decision moves. The last one
                      runs to a stage that was never built, so it is dashed. */}
                  <span
                    aria-hidden
                    className={`absolute bottom-0 left-[15px] top-8 w-0 border-l ${
                      i === WORKFLOW.length - 1 ? "border-dashed border-unavail/60" : "border-line"
                    }`}
                  />
                  <span
                    aria-hidden
                    className="numeral relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-court text-[12px] font-medium text-signal"
                  >
                    {i + 1}
                  </span>
                  <div className="min-w-0 pt-0.5">
                    <h3 className="font-serif text-[1.5rem] leading-[1.1] text-foreground">{step.stage}</h3>
                    <p className="mt-1.5 text-[14.5px] leading-relaxed text-muted">
                      <span className="sr-only">{step.stage} — </span>
                      {step.blurb.charAt(0).toUpperCase() + step.blurb.slice(1)}
                    </p>
                    <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-faint">
                      <span>In</span>
                      {step.surfaces.map((surface, j) => (
                        <span key={surface.name} className="flex items-center gap-2">
                          <Link
                            href={surface.href}
                            className="whitespace-nowrap text-muted underline decoration-line underline-offset-4 transition-colors hover:text-signal hover:decoration-signal/50"
                          >
                            {surface.name}
                          </Link>
                          {/* The separator trails its link, so a wrapped line opens on a name. */}
                          {j < step.surfaces.length - 1 && <span aria-hidden>·</span>}
                        </span>
                      ))}
                    </p>
                  </div>
                </li>
              ))}
              <li className="relative flex gap-5">
                <span
                  aria-hidden
                  className="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-dashed border-unavail bg-court text-[13px] text-unavail"
                >
                  —
                </span>
                <div className="min-w-0 pt-0.5">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                    <h3 className="font-serif text-[1.5rem] leading-[1.1] text-muted">Contract Predictor</h3>
                    <Badge status="unavailable">not shipped</Badge>
                  </div>
                  <p className="mt-1.5 text-[14.5px] leading-relaxed text-muted">
                    Deliberately not shipped: it needs historical contract data before a validated
                    model can exist, and Pivot doesn&apos;t ship fake models.
                  </p>
                </div>
              </li>
            </ol>
          </section>

          {/* ------------------------------------------------- architecture */}
          <section aria-labelledby="about-architecture" className="space-y-6">
            <SectionHead eyebrow="How it is built" title="Architecture" id="about-architecture" />
            <Architecture />
          </section>
        </div>

        {/* ------------------------------------------------------- aside */}
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-[calc(var(--nav-h)+2rem)] lg:self-start">
          <section aria-labelledby="about-honesty" className="panel px-5 py-5" style={{ "--edge": "var(--legal)" } as React.CSSProperties}>
            <div className="eyebrow">The standard</div>
            <h2 id="about-honesty" className="mt-2 font-serif text-[1.6rem] leading-[1.1] text-foreground">
              Data honesty, by design
            </h2>
            <ul className="mt-4 space-y-3">
              {HONESTY.map((line) => (
                <li key={line} className="flex gap-3 text-[13.5px] leading-relaxed text-muted">
                  <span aria-hidden className="mt-px shrink-0 text-[12px] text-legal">
                    ✓
                  </span>
                  <span className="min-w-0">{line}</span>
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="about-repository" className="sheet px-5 py-4">
            <h2 id="about-repository" className="eyebrow">
              Repository
            </h2>
            <p className="mt-2.5 text-[13px] leading-relaxed text-muted">
              Full source, methodology, model cards, CBA rule coverage, decision log (ADRs), identity
              resolution and deployment guide live in the repository — start with the README, then{" "}
              <code className="text-[12px] text-foreground">docs/architecture.md</code>{" "}and{" "}
              <code className="text-[12px] text-foreground">docs/methodology.md</code>. See{" "}
              <Link
                href="/methodology"
                className="text-signal underline decoration-signal/40 underline-offset-4 hover:decoration-signal"
              >
                Methodology
              </Link>{" "}
              for how each number is produced, and{" "}
              <Link
                href="/data-health"
                className="text-signal underline decoration-signal/40 underline-offset-4 hover:decoration-signal"
              >
                Data Health
              </Link>{" "}
              for what is currently ingested.
            </p>
            <p className="mt-3.5 border-t border-hairline pt-3.5 text-[12.5px] leading-relaxed text-muted">
              Pivot is independent and not affiliated with the NBA; team marks and player images are
              used locally for identification.
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}
