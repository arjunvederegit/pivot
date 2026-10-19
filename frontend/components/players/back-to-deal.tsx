"use client";

/**
 * R9.3A — when a player file was opened from a trade participant, a way back to that deal. The Trade Evaluator
 * keeps the scenario in its address (lib/investigation.ts), so going Back restores it; this is the visible half.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  if (params.get("from") !== "trade-evaluator") return null;
  return (
    <div className="mb-4">
      <button
        type="button"
        onClick={() => router.back()}
        className="inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-[12.5px] font-medium text-muted hover:border-signal hover:text-signal"
      >
        <span aria-hidden>←</span> Back to your trade
      </button>
    </div>
  );
}

export function BackToDeal() {
  return (
    <Suspense fallback={null}>
      <Inner />
    </Suspense>
  );
}
