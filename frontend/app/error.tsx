"use client";

/**
 * Route-level error boundary.
 *
 * The honesty standard applies to failure too: say what happened, say what it does not
 * tell you, and offer the next step. It never prints a stack trace at the reader —
 * `error.message` is shown because it is often specific enough to be useful, and
 * `error.digest` is shown because that is the one string a developer needs to find this
 * exact failure in the server logs.
 *
 * The page opens like every other route — eyebrow, serif title, one plain paragraph —
 * then shows the failure itself in a quiet card. "Back to Command Center" goes to `/`,
 * the home dashboard (`/command-center` is an alias of it).
 *
 * No fetching and no query client here — a boundary that can fail while recovering is
 * worse than none.
 */

import Link from "next/link";
import { Button, ButtonLink } from "@/components/ui";

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const detail = error.message?.trim();

  return (
    <div className="mx-auto max-w-3xl">
      <header className="border-b border-hairline pb-7">
        <div className="eyebrow flex items-center gap-2 text-illegal">
          <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-illegal" />
          Error · this view
        </div>
        <h1 className="title-xl mt-3 text-balance text-foreground">This view did not load</h1>
        <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-muted">
          Something failed while this page was being built, so Pivot stopped instead of showing a
          partial view. Nothing was changed by the attempt, and the rest of the app is unaffected —
          the other sections still load normally.
        </p>
      </header>

      <section
        aria-labelledby="error-what-failed"
        className="panel mt-8 overflow-hidden"
        style={{ "--edge": "var(--illegal)" } as React.CSSProperties}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-hairline px-5 py-3.5">
          <h2 id="error-what-failed" className="title-sm text-foreground">
            What failed
          </h2>
          <span className="text-[12px] text-faint">route boundary</span>
        </div>

        <div className="px-5 py-5">
          {detail ? (
            <pre className="whitespace-pre-wrap break-words rounded-lg border border-hairline bg-court px-4 py-3 text-[12.5px] leading-relaxed text-foreground">
              {detail}
            </pre>
          ) : (
            <p className="text-[13.5px] text-muted">The failure carried no message.</p>
          )}

          {error.digest && (
            <p className="mt-3.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-faint">
              <span className="font-medium text-muted">Digest</span>
              <code className="text-[12px] text-foreground">{error.digest}</code>
              <span aria-hidden>·</span>
              <span>quote this when reporting the failure</span>
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-hairline px-5 py-4">
          <Button variant="primary" onClick={() => reset()}>
            Try again
          </Button>
          <ButtonLink href="/" variant="secondary">
            Back to Command Center
          </ButtonLink>
        </div>
      </section>

      <p className="mt-5 max-w-2xl text-[13px] leading-relaxed text-muted">
        Trying again re-runs the page. If it fails the same way twice, the cause is upstream of the
        browser — usually the API being unreachable or a record the page needs not being imported
        yet.{" "}
        <Link
          href="/data-health"
          className="text-signal underline decoration-signal/40 underline-offset-4 hover:decoration-signal"
        >
          Data Health
        </Link>{" "}
        reports which sources are currently loaded.
      </p>
    </div>
  );
}
