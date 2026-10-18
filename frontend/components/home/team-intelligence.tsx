"use client";

/**
 * Team intelligence — the dashboard's read of the user's own franchise.
 *
 * Everything here is served by the existing API and shown as served: the team's ratings
 * with their NBA.com league ranks, the measured strengths and weaknesses exactly as the
 * server classified them (with its own explanation sentence), the roster's composition by
 * archetype family, and the cap position — which, without imported contracts, is
 * reported as unavailable rather than estimated.
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import type { FavoriteTeam } from "@/lib/favoriteTeam";
import { NEED_LABEL, ordinal } from "@/lib/format";
import { teamProfileSchema } from "@/lib/schemas";
import { teamIdentity } from "@/lib/teamIdentity";
import type { AcquisitionTarget, TeamProfile } from "@/lib/types";
import { IconArrowRight } from "@/components/icons";
import { ImpactInline } from "@/components/impact";
import { TeamLogo } from "@/components/media";
import { Badge, ErrorState, Skeleton, SourceRail, UnavailableNotice } from "@/components/ui";

interface TeamDetail {
  team: { id: string; abbreviation: string; full_name: string };
  season: string;
  standing: {
    wins: number;
    losses: number;
    conference: string | null;
    playoff_rank: number | null;
    details?: Record<string, unknown> | null;
  } | null;
  stats: { advanced?: Record<string, number | null> } | null;
  stats_retrieved_at: string | null;
}

interface CapOutlook {
  available: boolean;
  reason?: string;
}

interface Targets {
  available: boolean;
  unavailable_reason?: string;
  target_need: { need_key: string; explanation: string } | null;
  targets: AcquisitionTarget[];
}

const FAMILY_ORDER = ["guard", "wing", "big"];
const FAMILY_TONE: Record<string, string> = {
  guard: "var(--signal)",
  wing: "#a9c7d6",
  big: "#d6c39b",
};

function signed(x: number | null | undefined, digits = 1) {
  if (x === null || x === undefined) return "—";
  const r = Number(x.toFixed(digits));
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(digits)}`;
}

function RatingCell({
  label,
  value,
  rank,
  hint,
}: {
  label: string;
  value: string;
  rank: number | null | undefined;
  hint: string;
}) {
  return (
    <div className="min-w-0 bg-panel px-4 py-3.5" title={hint}>
      <dt className="eyebrow">{label}</dt>
      <dd className="mt-1.5 flex items-baseline gap-2">
        <span className="stat-serif text-[1.85rem] leading-none text-foreground">{value}</span>
        {typeof rank === "number" && (
          <span className="text-[11.5px] text-muted">{ordinal(rank)} of 30</span>
        )}
      </dd>
    </div>
  );
}

export function TeamIntelligence({ favorite }: { favorite: FavoriteTeam }) {
  const id = favorite.id;
  const identity = teamIdentity(favorite.abbreviation);

  const detail = useQuery({
    queryKey: ["team-detail", id],
    queryFn: () => api.get<TeamDetail>(`/teams/${id}`),
    staleTime: 300_000,
  });
  const profile = useQuery({
    queryKey: ["team-profile", id],
    queryFn: () => api.get<TeamProfile>(`/intelligence/teams/${id}/profile`, teamProfileSchema),
    staleTime: 300_000,
  });
  const cap = useQuery({
    queryKey: ["cap-outlook-summary", id],
    queryFn: () => api.get<CapOutlook>(`/teams/${id}/cap-outlook`),
    staleTime: 300_000,
  });

  const adv = detail.data?.stats?.advanced ?? {};
  const standing = detail.data?.standing ?? null;
  const families = (profile.data?.archetype_distribution ?? []).reduce<Record<string, number>>(
    (acc, row) => {
      const fam = row.family ?? "unclassified";
      acc[fam] = (acc[fam] ?? 0) + row.count;
      return acc;
    },
    {},
  );
  const familyTotal = Object.values(families).reduce((a, b) => a + b, 0);
  const familyKeys = [
    ...FAMILY_ORDER.filter((f) => families[f]),
    ...Object.keys(families).filter((f) => !FAMILY_ORDER.includes(f)),
  ];

  return (
    <section
      aria-labelledby="team-intel-heading"
      className="panel overflow-hidden"
      style={{ "--edge": identity.bright } as React.CSSProperties}
    >
      <header
        className="flex flex-wrap items-center justify-between gap-4 border-b border-hairline px-5 py-4"
        style={{ background: `linear-gradient(105deg, ${identity.primary}30, transparent 60%)` }}
      >
        <div className="flex min-w-0 items-center gap-3.5">
          <TeamLogo abbreviation={favorite.abbreviation} size={40} />
          <div className="min-w-0">
            <div className="eyebrow">Team intelligence</div>
            <h2 id="team-intel-heading" className="mt-1 truncate font-serif text-[1.65rem] leading-tight text-foreground">
              {detail.data?.team.full_name ?? identity.name}
            </h2>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {standing && (
            <p className="text-[13px] text-muted">
              <span className="numeral text-foreground">
                {standing.wins}–{standing.losses}
              </span>
              {standing.playoff_rank ? ` · ${ordinal(standing.playoff_rank)} in the ${standing.conference ?? ""}` : ""}
            </p>
          )}
          <Link
            href={`/team-outlook/${id}`}
            className="flex items-center gap-1.5 text-[13px] font-medium text-signal hover:text-foreground"
          >
            Team Outlook <IconArrowRight size={15} />
          </Link>
        </div>
      </header>

      {detail.error ? (
        <div className="p-5">
          <ErrorState message={`Could not load the team: ${String(detail.error)}`} />
        </div>
      ) : (
        <dl className="grid grid-cols-2 gap-px border-b border-hairline bg-hairline lg:grid-cols-4">
          {detail.data ? (
            <>
              <RatingCell
                label="Offense"
                value={adv.OFF_RATING != null ? adv.OFF_RATING.toFixed(1) : "—"}
                rank={adv.OFF_RATING_RANK}
                hint="Points scored per 100 possessions · league rank, 1 = best"
              />
              <RatingCell
                label="Defense"
                value={adv.DEF_RATING != null ? adv.DEF_RATING.toFixed(1) : "—"}
                rank={adv.DEF_RATING_RANK}
                hint="Points allowed per 100 possessions (lower is better) · league rank, 1 = best"
              />
              <RatingCell
                label="Net rating"
                value={signed(adv.NET_RATING)}
                rank={adv.NET_RATING_RANK}
                hint="Point margin per 100 possessions · league rank, 1 = best"
              />
              <RatingCell
                label="Pace"
                value={adv.PACE != null ? adv.PACE.toFixed(1) : "—"}
                rank={adv.PACE_RANK}
                hint="Possessions per 48 minutes · rank 1 = fastest"
              />
            </>
          ) : (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="bg-panel px-4 py-3.5">
                <Skeleton className="h-12" />
              </div>
            ))
          )}
        </dl>
      )}

      <div className="grid gap-px bg-hairline lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        {/* strengths and weaknesses, as the server classified them */}
        <div className="bg-panel p-5">
          {profile.error ? (
            <ErrorState message={`Could not load the team profile: ${String(profile.error)}`} />
          ) : !profile.data ? (
            <Skeleton className="h-40" />
          ) : !profile.data.needs_available ? (
            <UnavailableNotice reason={profile.data.needs_unavailable_reason} />
          ) : (
            <div className="grid gap-6 sm:grid-cols-2">
              {[
                { title: "Strengths", rows: profile.data.strengths, tone: "text-legal", glyph: "▲" },
                { title: "Weaknesses", rows: profile.data.weaknesses, tone: "text-illegal", glyph: "▼" },
              ].map(({ title, rows, tone, glyph }) => (
                <div key={title} className="min-w-0">
                  <h3 className="eyebrow">{title}</h3>
                  {rows.length === 0 ? (
                    <p className="mt-2.5 text-[13px] text-muted">None measured this season.</p>
                  ) : (
                    <ul className="mt-2.5 space-y-3">
                      {rows.slice(0, 3).map((row) => (
                        <li key={row.key} className="min-w-0">
                          <p className="flex items-baseline gap-2 text-[14px] font-medium text-foreground">
                            <span aria-hidden className={`text-[10px] ${tone}`}>
                              {glyph}
                            </span>
                            {row.label}
                          </p>
                          <p className="mt-0.5 pl-[18px] text-[12.5px] leading-snug text-muted">
                            {row.explanation}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* roster composition and cap position */}
        <div className="space-y-5 bg-panel p-5">
          <div>
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="eyebrow">Roster composition</h3>
              {profile.data && (
                <span className="text-[11.5px] text-faint">{profile.data.roster_size} players</span>
              )}
            </div>
            {!profile.data ? (
              <Skeleton className="mt-3 h-10" />
            ) : familyTotal === 0 ? (
              <p className="mt-2.5 text-[13px] text-muted">No archetypes assigned this season.</p>
            ) : (
              <>
                <div
                  className="mt-3 flex h-2.5 overflow-hidden rounded-full bg-panel3"
                  role="img"
                  aria-label={familyKeys.map((f) => `${families[f]} ${f}`).join(", ")}
                >
                  {familyKeys.map((f) => (
                    <span
                      key={f}
                      style={{
                        width: `${(families[f] / familyTotal) * 100}%`,
                        background: FAMILY_TONE[f] ?? "var(--unknown)",
                      }}
                      className="h-full border-r border-panel last:border-r-0"
                    />
                  ))}
                </div>
                <ul className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
                  {familyKeys.map((f) => (
                    <li key={f} className="flex items-center gap-1.5">
                      <span
                        aria-hidden
                        className="h-2 w-2 rounded-full"
                        style={{ background: FAMILY_TONE[f] ?? "var(--unknown)" }}
                      />
                      <span className="capitalize">{f}s</span>
                      <span className="numeral text-foreground">{families[f]}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <div className="border-t border-hairline pt-4">
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="eyebrow">Cap position</h3>
              <Link href={`/salary-cap-center?team=${id}`} className="text-[12px] text-signal hover:text-foreground">
                Cap Center
              </Link>
            </div>
            {!cap.data ? (
              <Skeleton className="mt-3 h-8" />
            ) : cap.data.available ? (
              <p className="mt-2 text-[13px] text-muted">
                Contracts on file — payroll, cap space and apron position are in the Cap Center.
              </p>
            ) : (
              <div className="mt-2.5">
                <Badge status="unavailable">not imported</Badge>
                <p className="mt-2 text-[13px] leading-snug text-muted">
                  Payroll and apron position stay unavailable until contracts are imported — never
                  estimated.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="px-5 pb-4">
        <SourceRail
          source="NBA.com team stats · Pivot team profile"
          extra={
            profile.data ? (
              <>
                <span aria-hidden>·</span>
                <span>{profile.data.season}</span>
              </>
            ) : undefined
          }
        />
      </div>
    </section>
  );
}

/**
 * Who answers the team's most severe measured need — the acquisition model's own top
 * candidates, quoted with its own numbers. The wins figure is the model's point estimate
 * for adding the player alone; this search does not serve a band, and the card says so.
 */
export function NeedAnswers({ favorite }: { favorite: FavoriteTeam }) {
  const id = favorite.id;
  const { data, error, isPending } = useQuery({
    queryKey: ["acquisition-targets-home", id],
    queryFn: () => api.get<Targets>(`/teams/${id}/acquisition-targets?limit=5`),
    staleTime: 300_000,
  });
  const need = data?.target_need;

  return (
    <section aria-labelledby="need-heading" className="panel flex flex-col p-5">
      <div className="eyebrow">Biggest need · who answers it</div>
      <h2 id="need-heading" className="mt-1.5 font-serif text-[1.45rem] leading-tight text-foreground">
        {need ? (NEED_LABEL[need.need_key] ?? need.need_key.replaceAll("_", " ")) : "Acquisition targets"}
      </h2>
      {need && <p className="mt-1 text-[12.5px] leading-snug text-muted">{need.explanation}</p>}

      <div className="mt-4 flex-1">
        {error ? (
          <ErrorState message={`Could not run the acquisition search: ${String(error)}`} />
        ) : isPending ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : !data?.available ? (
          <UnavailableNotice reason={data?.unavailable_reason ?? "No search was run for this roster."} />
        ) : data.targets.length === 0 ? (
          <p className="text-[13px] text-muted">No player cleared the search&apos;s conditions for this need.</p>
        ) : (
          <ol className="divide-y divide-hairline">
            {/* Five where the card stands beside the team panel and has the height for them;
                three where it stacks. */}
            {data.targets.slice(0, 5).map((t, i) => (
              <li key={t.player_id} className={`${i >= 3 ? "hidden xl:flex" : "flex"} items-center gap-3 py-2.5`}>
                <TeamLogo abbreviation={t.team.abbreviation} size={26} decorative />
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/players/${t.player_id}`}
                    className="block truncate text-[14px] font-medium text-foreground hover:text-signal"
                  >
                    {t.name}
                  </Link>
                  <p className="truncate text-[12px] text-faint">
                    {t.team.abbreviation} · skill at the {ordinal(t.skill_percentile * 100)} pctl
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <ImpactInline tei={t.tei} />
                  <p
                    className="text-[11.5px] text-muted"
                    title="The model's point estimate for adding this player alone, over a full 82-game season. This search does not serve a 10–90% band; the Trade Evaluator does."
                  >
                    {t.projected_delta_wins != null ? `${signed(t.projected_delta_wins)} wins*` : "wins —"}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
      <p className="mt-3 border-t border-hairline pt-3 text-[11.5px] leading-snug text-faint">
        *Point estimate for adding the player alone — this search serves no band. Test a real
        package in the{" "}
        <Link href={`/trade-evaluator?team=${id}`} className="text-signal hover:text-foreground">
          Trade Evaluator
        </Link>
        , which shows the 10th–90th percentile range.
      </p>
    </section>
  );
}
