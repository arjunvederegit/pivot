/**
 * Route-level not-found boundary.
 *
 * A 404 is a real answer, not a failure, so it is designed like one: the ball out of
 * bounds, a plain statement of what happened, and the destinations a reader actually
 * wants next. It states the two ordinary causes — a mistyped address, or an id that
 * matches nothing in the imported data — rather than implying the app is broken.
 *
 * "Command Center" is the home dashboard: the link goes to `/` (`/command-center` is an
 * alias that serves the same page, so a second "home" link would only repeat it).
 *
 * Server component by design: no fetching, no client state, nothing that can fail a
 * second time.
 */

import Link from "next/link";
import { BallGlyph, HalfCourt } from "@/components/court";
import { IconArrowRight, IconHome, IconScouting, IconTeams, IconTrade } from "@/components/icons";
import { ButtonLink } from "@/components/ui";

const DESTINATIONS: {
  href: string;
  label: string;
  hint: string;
  icon: (p: { size?: number; className?: string }) => React.ReactNode;
}[] = [
  { href: "/", label: "Command Center", hint: "League state, your team and the decision workflow", icon: IconHome },
  { href: "/player-explorer", label: "Players", hint: "Stat lines, league impact and comparison", icon: IconScouting },
  { href: "/team-outlook", label: "Teams", hint: "Roster, strengths, needs and payroll", icon: IconTeams },
  { href: "/trade-evaluator", label: "Trade Evaluator", hint: "Build a deal and test it", icon: IconTrade },
];

/** The half court with the ball past the sideline — where a dead address lands. */
function OutOfBounds() {
  return (
    <div className="relative mx-auto aspect-[600/470] w-full max-w-[400px]">
      <HalfCourt className="absolute inset-y-0 left-0 h-full w-[83.333%] text-foreground/45" />
      <svg viewBox="0 0 600 470" className="absolute inset-0 h-full w-full" fill="none" aria-hidden>
        {/* the pass: from the top of the key, over the sideline */}
        <path
          d="M250 250C360 150 470 120 556 168"
          stroke="var(--signal)"
          strokeWidth="1.5"
          strokeDasharray="5 6"
          vectorEffect="non-scaling-stroke"
        />
        <circle cx="250" cy="250" r="4" fill="var(--signal)" />
        {/* the sideline it crossed */}
        <path d="M499 124v50" stroke="var(--illegal)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <span className="absolute left-[92.6%] top-[35.7%] -translate-x-1/2 -translate-y-1/2 text-signal">
        <BallGlyph size={22} />
      </span>
    </div>
  );
}

export default function NotFound() {
  return (
    <div className="mx-auto max-w-5xl">
      <header className="border-b border-hairline pb-7">
        <div className="eyebrow flex items-center gap-2">
          <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-signal" />
          404 · Page not found
        </div>
        <h1 className="title-xl mt-3 text-balance text-foreground">There is no page at this address</h1>
        <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-muted">
          Two things usually cause it: the address was mistyped or truncated, or it points at a
          player, team or trade id that does not exist in the data Pivot has imported. Either way
          nothing failed — the app was asked for something it does not have.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-2">
          <ButtonLink href="/" variant="primary">
            Back to Command Center
          </ButtonLink>
          <ButtonLink href="/data-health" variant="secondary">
            Check what data is loaded
          </ButtonLink>
        </div>
      </header>

      <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <figure className="panel flex flex-col justify-between p-5">
          <div className="flex items-baseline justify-between gap-2">
            <span className="eyebrow">Out of bounds</span>
            <span className="text-[12px] text-faint">HTTP 404</span>
          </div>
          <div className="py-5">
            <OutOfBounds />
          </div>
          <figcaption className="border-t border-hairline pt-3.5 text-[12.5px] leading-relaxed text-muted">
            The address points past the edge of the floor Pivot has mapped. The court is fine — the
            ball just went somewhere there is no page.
          </figcaption>
        </figure>

        <nav aria-labelledby="not-found-destinations" className="panel overflow-hidden">
          <div className="border-b border-hairline px-5 py-4">
            <div className="eyebrow">Main destinations</div>
            <h2 id="not-found-destinations" className="mt-1.5 font-serif text-[1.6rem] leading-tight text-foreground">
              Where to go instead
            </h2>
          </div>
          <ul className="divide-y divide-hairline">
            {DESTINATIONS.map(({ href, label, hint, icon: Icon }) => (
              <li key={href}>
                <Link
                  href={href}
                  className="group flex items-center gap-4 px-5 py-4 transition-colors hover:bg-panel2 focus-visible:outline-offset-[-3px]!"
                >
                  <span
                    aria-hidden
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-hairline bg-panel2 text-muted transition-colors group-hover:text-signal"
                  >
                    <Icon size={18} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="title-md block text-foreground group-hover:text-signal">{label}</span>
                    <span className="mt-0.5 block text-[13px] leading-snug text-muted">{hint}</span>
                  </span>
                  <IconArrowRight
                    size={16}
                    className="shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-signal"
                  />
                </Link>
              </li>
            ))}
          </ul>
          <p className="border-t border-hairline px-5 py-3.5 text-[12.5px] leading-relaxed text-faint">
            Followed a link from inside Pivot? Then the record it pointed at is no longer in the
            database. Data Health lists which sources are imported and when each was last refreshed.
          </p>
        </nav>
      </div>
    </div>
  );
}
