"use client";

/**
 * Team Outlook — one franchise, read in the order a front office reads it:
 *
 *   1. who they are and where they stand — the masthead and the season line, which ends
 *      with the server's own headline needs and strengths in one line;
 *   2. the diagnosis — every team statistic on one league axis beside what the rotation's
 *      skills already cover, on the same 0–100 scale;
 *   3. the mandate (team strategy) every trade for them is scored against;
 *   4. who answers the needs;
 *   5. the roster itself: placed in the league, its composition, every player by position;
 *   6. money and assets.
 *
 * Every number on the page is served. Strengths and needs are classified on the server;
 * impact is placed in the league with the shared impact components; anything missing is
 * shown as missing. Team colour is an accent only — the logo, a thin line along the top
 * of the masthead and of each card.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { count, money, payrollDisclosure } from "@/lib/format";
import { teamProfileSchema } from "@/lib/schemas";
import { teamIdentity, teamVars } from "@/lib/teamIdentity";
import type { PayrollResponse, RosterResponse, Scenario, Team, TeamProfile, Vocabulary } from "@/lib/types";
import { AcquisitionTargetsPanel } from "@/components/acquisition";
import { IconArrowRight } from "@/components/icons";
import { TeamLogo } from "@/components/media";
import { CommandText } from "@/components/reference/format";
import { useToast } from "@/components/toast";
import { Composition } from "@/components/teams/composition";
import { RosterDepth } from "@/components/teams/roster-depth";
import { RosterOnLeague } from "@/components/teams/roster-league";
import { SeasonContextLine, SeasonBasisNotice } from "@/components/season-context";
import { SeasonLine, type TeamDetail } from "@/components/teams/season-line";
import { SectionHead } from "@/components/teams/section-head";
import { SkillCoveragePanel } from "@/components/teams/skill-coverage";
import { StrengthsNeeds } from "@/components/teams/strengths-needs";
import { TeamPanel } from "@/components/teams/team-panel";
import {
  Badge,
  Button,
  ButtonLink,
  EmptyState,
  ErrorState,
  PageHeader,
  Panel,
  Skeleton,
  SkeletonRows,
  SourceRail,
  StatBlock,
  UnavailableNotice,
} from "@/components/ui";

const STRATEGIES = [
  ["contend", "Contend now", "Win this season; future flexibility is secondary."],
  ["improve", "Improve, keep flexibility", "Get better without mortgaging the books."],
  ["retool", "Re-tool around the core", "Change the supporting cast, keep the stars."],
  ["rebuild", "Rebuild", "Trade present value for future value."],
  ["youth", "Chase young upside", "Prioritize age and development curves."],
  ["cap_relief", "Cut salary / tax", "Get under a line, accept on-court cost."],
] as const;

function windowLabel(avgAge: number | null): {
  label: string;
  hint: string;
  status: string;
} {
  if (avgAge === null)
    return {
      label: "Unknown",
      hint: "Roster ages are unavailable, so the window can't be inferred.",
      status: "unavailable",
    };
  if (avgAge < 25.5)
    return { label: "Ascending", hint: "Young rotation — the window is opening.", status: "info" };
  if (avgAge < 28.5)
    return { label: "Open now", hint: "Prime-age rotation — win-now moves fit.", status: "pass" };
  return {
    label: "Closing",
    hint: "Veteran rotation — weigh future flexibility carefully.",
    status: "warning",
  };
}

const mean = (xs: number[]) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null);

export default function TeamOutlookPage({ params }: { params: Promise<{ teamId: string }> }) {
  const { teamId } = use(params);
  const toast = useToast();
  const queryClient = useQueryClient();

  const { data: detail, error } = useQuery({
    queryKey: ["team", teamId],
    queryFn: () => api.get<TeamDetail>(`/teams/${teamId}`),
  });
  const { data: roster, error: rosterError } = useQuery({
    queryKey: ["roster", teamId],
    queryFn: () => api.get<RosterResponse>(`/teams/${teamId}/roster`),
  });
  // The roster profile: skill coverage, archetype distribution, and needs already sorted
  // into strengths and weaknesses. That classification is a basketball judgement and is
  // made once, on the server (`app/domain/needs.py`), so this page renders the rule rather
  // than deciding it — see the note at the top of `lib/needs.ts`.
  const { data: profile, error: profileError } = useQuery({
    queryKey: ["team-profile", teamId],
    queryFn: () =>
      api.get<TeamProfile>(`/intelligence/teams/${teamId}/profile`, teamProfileSchema),
  });
  const { data: payroll, error: payrollError } = useQuery({
    queryKey: ["payroll", teamId],
    queryFn: () => api.get<PayrollResponse>(`/teams/${teamId}/payroll`),
  });
  // The published thresholds and need sources, so the axis draws the server's lines
  // rather than copies of them. Shared cache key with the home page.
  const { data: vocabulary } = useQuery({
    queryKey: ["intelligence-vocabulary"],
    queryFn: () => api.get<Vocabulary>("/intelligence/vocabulary"),
    staleTime: Infinity,
  });

  const [strategy, setStrategy] = useState<string>("contend");
  const saveStrategy = useMutation({
    mutationFn: () =>
      api.post<Scenario>("/scenarios", {
        name: `${detail?.team.abbreviation} — ${STRATEGIES.find(([value]) => value === strategy)?.[1]}`,
        focal_team_id: teamId,
        strategy,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scenarios"] });
      toast("success", "Strategy saved — it now drives trade evaluations for this team.");
    },
    onError: (e) => toast("error", `Could not save strategy: ${String(e)}`),
  });

  if (error) {
    if (error instanceof ApiError && error.status === 404) return <TeamNotFound teamId={teamId} />;
    return <ErrorState message={`Could not load team: ${String(error)}`} />;
  }
  if (!detail) return <TeamSkeleton />;

  const team = detail.team;
  const identity = teamIdentity(team.abbreviation);
  const rosterPlayers = roster?.roster ?? [];
  const nickname = team.nickname ?? identity.nickname;

  // Two different ages, labelled as two different things: the whole listed roster, and
  // the eight highest-impact players, whose average sets the competitive window.
  const rosterAge = mean(rosterPlayers.map((p) => p.age).filter((a): a is number => a !== null));
  const topEight = [...rosterPlayers].sort((a, b) => (b.tei ?? -99) - (a.tei ?? -99)).slice(0, 8);
  const topEightIds = new Set(topEight.map((p) => p.player_id));
  const windowAge = mean(topEight.map((p) => p.age).filter((a): a is number => a !== null));
  const competitiveWindow = windowLabel(windowAge);

  return (
    <div className="space-y-12 lg:space-y-14" style={teamVars(team.abbreviation)}>
      <div className="space-y-5">
        {/* ------------------------------------------------------------ masthead */}
        <header className="relative border-b border-hairline pb-6">
          {/* the franchise's colour, once, as a thin rule above the name */}
          <span
            aria-hidden
            className="absolute left-0 top-0 h-px w-full max-w-[28rem]"
            style={{ background: `linear-gradient(90deg, ${identity.bright}, transparent)` }}
          />
          <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5 pt-6">
            <div className="flex min-w-0 items-center gap-4 sm:gap-5">
              <TeamLogo abbreviation={team.abbreviation} name={team.full_name} size={68} />
              <div className="min-w-0">
                <div className="eyebrow flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: identity.bright }}
                  />
                  <span className="text-foreground">{team.abbreviation}</span>
                  <span aria-hidden className="text-faint">
                    ·
                  </span>
                  <span>
                    {team.conference ?? "—"}
                    {team.division ? ` · ${team.division}` : ""}
                  </span>
                  <span aria-hidden className="text-faint">
                    ·
                  </span>
                  <span>
                    {detail.season_context
                      ? `${detail.season_context.roster.season} roster · ${detail.season} statistics`
                      : detail.season}
                  </span>
                </div>
                <h1 className="title-xl mt-2 text-balance text-foreground">{team.full_name}</h1>
              </div>
            </div>
            <div>
              {/* The team being viewed — not necessarily the selected organisation. */}
              <p className="eyebrow mb-1.5 sm:text-right">Actions for {team.abbreviation}</p>
              <div className="flex flex-wrap items-center gap-2">
                <ButtonLink href={`/trade-evaluator?team=${teamId}`} variant="primary">
                  Start a trade
                </ButtonLink>
                <ButtonLink href={`/player-explorer?team=${teamId}`}>Compare players</ButtonLink>
                <ButtonLink href={`/salary-cap-center?team=${teamId}`}>Salary-Cap Center</ButtonLink>
              </div>
            </div>
          </div>
          <SeasonContextLine
            context={detail.season_context}
            statisticsSeason={detail.season}
            className="mt-4"
          />
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <Badge status={competitiveWindow.status}>window: {competitiveWindow.label}</Badge>
            <span className="text-[14px] text-muted">{competitiveWindow.hint}</span>
          </div>
        </header>

        {/* --------------------------------------------------------- season line */}
        <SeasonLine
          detail={detail}
          rosterCount={roster ? rosterPlayers.length : null}
          rosterAge={rosterAge}
          windowAge={windowAge}
          windowLabelText={competitiveWindow.label}
          profile={profile}
          profileError={profileError}
          vocabulary={vocabulary}
        />
      </div>

      {/* ----------------------------------------------------------- diagnosis */}
      <section id="diagnosis" aria-labelledby="diagnosis-heading" className="scroll-mt-24">
        <SectionHead
          id="diagnosis-heading"
          eyebrow={`Diagnosis · ${detail.season}`}
          title={`Where the ${nickname} stand`}
          aside={
            <>
              Team statistics and the rotation&apos;s skills on the same{" "}
              <span className="whitespace-nowrap">0–100</span> league scale — right is better, the
              median at 50.
            </>
          }
        />
        {/* Stacked below xl: side by side, a narrow column leaves each league track too
            short to read. From xl both cards stretch to the row and end together — but only
            when the axis has rows; a one-line notice is not stretched into an empty card. */}
        <div className={`grid gap-4 xl:grid-cols-12 ${profile?.needs_available ? "" : "xl:items-start"}`}>
          <TeamPanel
            className="flex flex-col xl:col-span-7"
            bodyClassName="flex flex-1 flex-col"
            title="Strengths & needs"
            subtitle="Each measured team statistic on one league axis. Tags are the server's percentile rules, not scouting opinions."
          >
            {profileError ? (
              <ErrorState message={`Could not load the roster profile: ${String(profileError)}`} />
            ) : !profile ? (
              <SkeletonRows rows={7} height="h-8" />
            ) : !profile.needs_available ? (
              <UnavailableNotice reason={<ServedSentence text={profile.needs_unavailable_reason} />} />
            ) : (
              <>
                <SeasonBasisNotice basis={profile.season_basis} />
                <StrengthsNeeds profile={profile} vocabulary={vocabulary} />
              </>
            )}
          </TeamPanel>

          <SkillCoveragePanel profile={profile} error={profileError} className="xl:col-span-5" />
        </div>
      </section>

      {/* ------------------------------------------------------------ mandate */}
      <section aria-labelledby="strategy-heading">
        <SectionHead
          id="strategy-heading"
          eyebrow="The mandate"
          title="Team strategy"
          aside="Drives how every trade is scored for this franchise. Save it, then start a trade."
        />
        <div className="panel p-4 sm:p-5">
          <fieldset className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            <legend className="sr-only">Choose a strategy for {team.full_name}</legend>
            {STRATEGIES.map(([value, label, hint]) => {
              const checked = strategy === value;
              return (
                <label
                  key={value}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3.5 py-3 transition-colors has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-signal ${
                    checked
                      ? "border-signal/60 bg-signal/8"
                      : "border-hairline bg-panel2/40 hover:border-line hover:bg-panel2"
                  }`}
                >
                  <input
                    type="radio"
                    name="strategy"
                    value={value}
                    checked={checked}
                    onChange={() => setStrategy(value)}
                    className="mt-[3px] h-3.5 w-3.5 shrink-0 accent-[var(--signal)] focus-visible:outline-none"
                  />
                  <span className="min-w-0">
                    <span
                      className={`block text-[14px] font-semibold leading-tight ${
                        checked ? "text-signal" : "text-foreground"
                      }`}
                    >
                      {label}
                    </span>
                    <span className="mt-1 block text-[12.5px] leading-snug text-muted">{hint}</span>
                  </span>
                </label>
              );
            })}
          </fieldset>
          <div className="mt-4 flex flex-col-reverse gap-3 border-t border-hairline pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-[12px] leading-relaxed text-faint">
              Fine-grained weights and untouchable players live in the Trade Evaluator.{" "}
              <Link href="/methodology#weights" className="text-signal underline underline-offset-2">
                How weights work
              </Link>
            </p>
            <Button
              variant="primary"
              className="w-full shrink-0 sm:w-auto sm:min-w-44"
              disabled={saveStrategy.isPending}
              onClick={() => saveStrategy.mutate()}
            >
              {saveStrategy.isPending ? "Saving…" : "Save strategy"}
            </Button>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------- answers to needs */}
      <AcquisitionTargetsPanel teamId={team.id} teamAbbr={team.abbreviation} />

      {/* ------------------------------------------------------------- roster */}
      <section aria-labelledby="roster-heading">
        <SectionHead
          id="roster-heading"
          eyebrow={roster ? `Personnel · ${count(rosterPlayers.length, "player")}` : "Personnel"}
          title="The roster"
          aside="Every player's impact estimate placed in the league, the roster's shape on the floor, then each player by listed position."
        />
        {/* Full width: the figure names every flag only when it has room for them, and a
            narrow column would fall back to initials on a desktop screen. */}
        <div className="space-y-4">
          <TeamPanel
            title="Roster on the league"
            subtitle="Every player's impact estimate, planted on the distribution of every player Pivot scored"
            actions={
              <Link
                href="/methodology#tei"
                className="inline-flex items-center gap-1.5 whitespace-nowrap text-[12.5px] font-medium text-signal hover:text-foreground"
              >
                What is impact?
                <IconArrowRight size={14} />
              </Link>
            }
          >
            {rosterError ? (
              <ErrorState message={`Could not load the roster: ${String(rosterError)}`} />
            ) : !roster ? (
              <Skeleton className="h-[230px]" />
            ) : rosterPlayers.length === 0 ? (
              <UnavailableNotice reason="No roster rows for this team, so nobody can be placed." />
            ) : (
              <RosterOnLeague roster={rosterPlayers} teamAbbr={team.abbreviation} />
            )}
          </TeamPanel>

          <TeamPanel title="Composition" subtitle="Listed position against functional role">
            {rosterError ? (
              <ErrorState message={`Could not load the roster: ${String(rosterError)}`} />
            ) : !roster ? (
              <SkeletonRows rows={6} height="h-8" />
            ) : (
              <Composition roster={rosterPlayers} profile={profile} profileError={profileError} />
            )}
          </TeamPanel>

          <TeamPanel
            padded={false}
            title={roster ? `Roster by position · ${rosterPlayers.length}` : "Roster by position"}
            subtitle="Grouped by listed position, sorted by impact within each group"
          >
            {rosterError ? (
              <div className="p-5">
                <ErrorState message={`Could not load the roster: ${String(rosterError)}`} />
              </div>
            ) : !roster ? (
              <div className="p-5">
                <SkeletonRows rows={8} height="h-14" />
              </div>
            ) : rosterPlayers.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  title="No roster rows for this team"
                  hint={
                    <>
                      Run <code className={CODE_CHIP}>make sync-data</code> on the backend to pull
                      the current roster from NBA.com.
                    </>
                  }
                  action={
                    <ButtonLink href="/data-health" size="sm">
                      Open Data Health
                    </ButtonLink>
                  }
                />
              </div>
            ) : (
              <RosterDepth
                players={rosterPlayers}
                topEightIds={topEightIds}
                profile={profile}
                source={roster.source}
                retrievedAt={roster.source_retrieved_at}
              />
            )}
          </TeamPanel>
        </div>
      </section>

      {/* ----------------------------------------------------- money & assets */}
      <section aria-labelledby="money-heading">
        <SectionHead
          id="money-heading"
          eyebrow="Money & assets"
          title="Payroll and draft capital"
          aside="Shown only from imported contracts and verified pick ownership — never estimated."
        />
        <div className="grid items-start gap-4 lg:grid-cols-2">
          <TeamPanel title="Payroll & cap status">
            {payrollError ? (
              <ErrorState message={`Could not load payroll: ${String(payrollError)}`} />
            ) : !payroll ? (
              <SkeletonRows rows={3} height="h-8" />
            ) : payroll.payroll_available ? (
              <div className="space-y-4">
                <StatBlock
                  label={`Committed payroll · ${payroll.league_year}`}
                  value={money(payroll.payroll)}
                  note={`${payroll.players_with_salary} of ${payroll.roster_size} players with a salary on file`}
                  size="lg"
                />
                {payroll.cap_context && (
                  <dl className="space-y-2 border-t border-hairline pt-3">
                    <MoneyRow label="Luxury-tax line" value={money(payroll.cap_context.luxury_tax)} />
                    <MoneyRow label="Room below tax" value={money(payroll.cap_context.room_below_tax)} />
                    <MoneyRow label="First apron" value={money(payroll.cap_context.first_apron)} />
                  </dl>
                )}
                <ButtonLink href={`/salary-cap-center?team=${teamId}`} size="sm" className="w-full">
                  Full picture in Salary-Cap Center
                </ButtonLink>
                {payroll.cap_context && (
                  <SourceRail source={payroll.cap_context.cap_source} retrievedAt={undefined} />
                )}
              </div>
            ) : payroll.payroll_known !== null && payroll.payroll_coverage.players_known > 0 ? (
              <PartialPayroll payroll={payroll} teamId={teamId} />
            ) : (
              <UnavailableNotice
                reason={
                  <ServedSentence
                    text={
                      payroll.unavailable_reason ??
                      "Contract data hasn't been imported, so payroll can't be computed."
                    }
                  />
                }
                steps={
                  <div className="space-y-2.5">
                    <p className="text-[12.5px] leading-relaxed text-muted">
                      Salary-matching rules will keep reporting <em>unavailable</em> until a
                      contract provider is configured. The import takes three steps, laid out in
                      the Salary-Cap Center.
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <ButtonLink href={`/salary-cap-center?team=${teamId}`} size="sm">
                        Contract import steps
                      </ButtonLink>
                      <ButtonLink href="/data-health" size="sm">
                        Open Data Health
                      </ButtonLink>
                    </div>
                  </div>
                }
              />
            )}
          </TeamPanel>

          <TeamPanel title="Draft capital">
            <UnavailableNotice reason="Verified draft-pick ownership isn't configured, and Pivot won't guess it. Hypothetical picks can still be added inside a trade, clearly labeled as hypothetical." />
          </TeamPanel>
        </div>
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

/** A shell command or env setting set inside a sentence: mono, on a faint chip, no backticks. */
const CODE_CHIP = "rounded bg-foreground/8 px-1 py-px font-mono text-[0.92em] text-foreground";

/**
 * A sentence written by the backend for whoever operates it, as it should read on a page.
 *
 * Two presentation changes, nothing else: `backtick` spans become code chips, and a
 * pointer into the repository ("See data/contracts/README.md to enable one.") is dropped,
 * because a file path is not something a reader can open from here. Every caller puts a
 * link to the page that shows those steps next to the sentence instead.
 */
const REPO_POINTER = /\s*See\s+`?[\w./-]+\.(?:md|txt|rst)`?(?:\s[^.]*)?\.?/gi;

function ServedSentence({ text }: { text: string }) {
  const shown = text.replace(REPO_POINTER, "").trim() || text;
  return (
    <span className="[overflow-wrap:anywhere]">
      <CommandText text={shown} codeClassName={CODE_CHIP} />
    </span>
  );
}

const THRESHOLD_LABEL: Record<string, string> = {
  salary_cap: "the salary cap",
  luxury_tax: "the luxury-tax line",
  first_apron: "the first apron",
  second_apron: "the second apron",
};

/**
 * Partial contract coverage (R2c). The figure shown is the sum of the contracts on file,
 * which is a *floor* — every missing salary can only raise it. The coverage, the count of
 * unpriced players and their names are rendered with the number, never behind a
 * disclosure the reader has to go looking for, and no cap position is claimed.
 */
function PartialPayroll({ payroll, teamId }: { payroll: PayrollResponse; teamId: string }) {
  const coverage = payroll.payroll_coverage;
  const cleared = payroll.cap_context_partial?.thresholds_already_cleared ?? [];
  const shown = payrollDisclosure(payroll.payroll, payroll.payroll_known, coverage);
  return (
    <div className="space-y-4">
      <StatBlock
        label={`Committed payroll · ${payroll.league_year}`}
        value={shown.value}
        note={shown.note}
        size="lg"
      />
      <div className="flex flex-wrap gap-1.5">
        <Badge status="unavailable">lower bound — {coverage.players_unknown} salaries missing</Badge>
        {cleared.length > 0 && (
          <Badge status="warning" glyph={false}>
            already above {THRESHOLD_LABEL[cleared[cleared.length - 1]] ?? cleared[cleared.length - 1]}
          </Badge>
        )}
      </div>
      <p className="text-[12.5px] leading-relaxed text-muted">
        <ServedSentence text={payroll.payroll_coverage_note} />
      </p>
      {payroll.cap_context_partial && (
        <dl className="space-y-2 border-t border-hairline pt-3">
          <MoneyRow label="Luxury-tax line" value={money(payroll.cap_context_partial.luxury_tax)} />
          <MoneyRow label="First apron" value={money(payroll.cap_context_partial.first_apron)} />
          <MoneyRow label="Room below tax" value="not computable" />
        </dl>
      )}
      {payroll.players_missing_salary.length > 0 && (
        <details className="text-[12.5px] text-muted">
          <summary className="cursor-pointer text-signal">
            {count(payroll.players_missing_salary.length, "player")} with no salary on file
          </summary>
          <ul className="mt-1.5 space-y-0.5 pl-4">
            {payroll.players_missing_salary.map((name) => (
              <li key={name} className="list-disc">
                {name}
              </li>
            ))}
          </ul>
        </details>
      )}
      <ButtonLink href={`/salary-cap-center?team=${teamId}`} size="sm" className="w-full">
        Full picture in Salary-Cap Center
      </ButtonLink>
      {payroll.cap_context_partial && (
        <SourceRail source={payroll.cap_context_partial.cap_source} retrievedAt={undefined} />
      )}
    </div>
  );
}

function MoneyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-[13px] text-muted">{label}</dt>
      <dd className="numeral shrink-0 text-[13.5px] text-foreground">{value}</dd>
    </div>
  );
}

/**
 * A team id that matches nothing is an answer, designed as one — not a red error box. It
 * keeps the page's own frame (header, then cards) so it reads as Team Outlook answering
 * "no such team", and it offers the franchises that do exist, so the page is a way
 * forward rather than a blank canvas.
 *
 * Rendered only after the team request has failed on the client, so reading the shared
 * `["teams"]` cache here cannot disturb hydration.
 */
function TeamNotFound({ teamId }: { teamId: string }) {
  const { data: teams } = useQuery({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
    staleTime: 300_000,
  });
  const franchises = [...(teams ?? [])].sort((a, b) => a.full_name.localeCompare(b.full_name));
  return (
    <div>
      <PageHeader
        eyebrow="Team Outlook"
        title="No team with this id"
        lede="The address may be mistyped, or point at a team that is not in the imported data. Pivot does not guess which franchise was meant."
      />
      <div className="space-y-6">
        <EmptyState
          title="Unknown team id"
          hint={
            <>
              Pivot has no franchise with the id{" "}
              <code className="break-words rounded bg-foreground/8 px-1 py-px font-mono text-[12px] text-foreground">
                {teamId}
              </code>
              . Open the league board to pick one, or check which teams were imported.
            </>
          }
          action={
            <>
              <ButtonLink href="/team-outlook" variant="primary">
                Back to the league board
              </ButtonLink>
              <ButtonLink href="/data-health">Check what data is loaded</ButtonLink>
            </>
          }
        />

        {franchises.length > 0 && (
          <Panel
            title="Open a franchise instead"
            subtitle={`${count(franchises.length, "franchise")} in the imported data, by name`}
          >
            <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
              {franchises.map((franchise) => (
                <li key={franchise.id} className="min-w-0">
                  <Link
                    href={`/team-outlook/${franchise.id}`}
                    className="group flex min-w-0 items-center gap-2.5 rounded-lg border border-hairline bg-panel2/45 py-2 pl-2 pr-3 transition-colors hover:border-line hover:bg-panel2"
                  >
                    <TeamLogo
                      abbreviation={franchise.abbreviation}
                      name={franchise.full_name}
                      size={28}
                      decorative
                    />
                    <span className="min-w-0 flex-1 truncate font-serif text-[1.15rem] leading-tight text-foreground group-hover:text-signal">
                      {franchise.nickname ?? franchise.full_name}
                    </span>
                    <span className="hidden shrink-0 text-[11px] font-medium tracking-[0.04em] text-faint sm:inline">
                      {franchise.abbreviation}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>
    </div>
  );
}

function TeamSkeleton() {
  return (
    <div className="space-y-5" role="status" aria-label="Loading team">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline pb-6 pt-6">
        <div className="flex min-w-0 flex-1 items-center gap-5">
          <Skeleton className="h-[68px] w-[68px]" />
          <div className="min-w-0 flex-1 space-y-3">
            <Skeleton className="h-3 w-48" />
            <Skeleton className="h-12 w-96 max-w-full" />
          </div>
        </div>
        <Skeleton className="h-10 w-80 max-w-full" />
      </div>
      <Skeleton className="h-[230px]" />
      <div className="space-y-4 pt-8">
        <Skeleton className="h-3 w-40" />
        <Skeleton className="h-9 w-80 max-w-full" />
        <div className="grid gap-4 xl:grid-cols-12">
          <Skeleton className="h-[520px] xl:col-span-7" />
          <Skeleton className="h-[520px] xl:col-span-5" />
        </div>
      </div>
    </div>
  );
}
