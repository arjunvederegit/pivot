/**
 * Route-level loading boundary.
 *
 * Every page in Pivot opens the same way: a quiet eyebrow, a serif title, a lede over a
 * hairline, then cards. This draws that shape while the route streams in, so a slow page
 * reads as the page arriving rather than as a spinner that says only "wait".
 *
 * Deliberately inert — no fetching, no providers, no client state. A loading boundary
 * that can itself fail is worse than none.
 */

import { Skeleton } from "@/components/ui";

function PanelSkeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={`panel overflow-hidden ${className ?? ""}`}>
      <div className="border-b border-hairline px-5 py-4">
        <Skeleton className="h-3 w-28 max-w-full" />
        <Skeleton className="mt-3 h-6 w-56 max-w-full" />
      </div>
      <div className="space-y-2.5 p-5">
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className="h-10" />
        ))}
      </div>
    </div>
  );
}

export default function Loading() {
  return (
    <div role="status" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading this view.</span>

      {/* Header — the same eyebrow, title and lede block PageHeader renders. */}
      <header className="mb-7 border-b border-hairline pb-6 lg:mb-9">
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
          <div className="min-w-0 flex-1">
            <div className="eyebrow flex items-center gap-2" aria-hidden>
              <span className="pulse-live h-1.5 w-1.5 shrink-0 rounded-full bg-signal" />
              Loading
            </div>
            <Skeleton className="mt-4 h-11 w-[22rem] max-w-full" />
            <Skeleton className="mt-4 h-3.5 w-[36rem] max-w-full" />
            <Skeleton className="mt-2.5 h-3.5 w-[28rem] max-w-full" />
          </div>
        </div>
      </header>

      {/* Figures — one card, cells divided by hairlines. */}
      <div className="panel mb-6 overflow-hidden">
        <div className="grid grid-cols-2 gap-px bg-hairline lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-panel px-5 py-4">
              <Skeleton className="h-2.5 w-20" />
              <Skeleton className="mt-3 h-8 w-16 max-w-full" />
              <Skeleton className="mt-3 h-2.5 w-28 max-w-full" />
            </div>
          ))}
        </div>
      </div>

      {/* Body: one wide column of detail, one narrower rail. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <PanelSkeleton rows={6} className="lg:col-span-2" />
        <PanelSkeleton rows={4} />
      </div>
    </div>
  );
}
