/**
 * /assistant — Pivot Intelligence (Ask Pivot), an experimental research partner over a
 * FICTIONAL scouting corpus.
 *
 * A server component on purpose: the build-time flag is checked here, before any client
 * code runs, so a build made without `NEXT_PUBLIC_ASSISTANT_ENABLED=true` serves the app's
 * ordinary not-found page at this address — no half-rendered chat, no client bundle doing
 * the gating, nothing that advertises a feature nobody switched on. With the flag on, the
 * client workspace takes over and asks the backend, at runtime, whether it will answer.
 *
 * One honest caveat: the root `app/loading.tsx` wraps every page in a Suspense boundary, so
 * `notFound()` here streams the not-found UI with HTTP 200 and a `robots: noindex` tag —
 * Next's documented behaviour for a not-found inside a streamed boundary. A true 404
 * status would need a check in `proxy.ts`, outside this route.
 */

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { AssistantWithHandover } from "@/components/assistant/handover";
import { ASSISTANT_ENABLED } from "@/lib/features";

/**
 * A function rather than a static `metadata` export: static metadata still applies when
 * the page calls `notFound()`, so a build without the flag would have served the ordinary
 * 404 under a "Pivot Intelligence" tab title — naming the feature it is meant to hide.
 */
export function generateMetadata(): Metadata {
  return ASSISTANT_ENABLED ? { title: "Ask Pivot" } : {};
}

export default function AssistantPage() {
  if (!ASSISTANT_ENABLED) notFound();
  // `useSearchParams` (the `?q=` handed over from the Workspace) needs a Suspense boundary.
  return (
    <Suspense>
      <AssistantWithHandover />
    </Suspense>
  );
}
