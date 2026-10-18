"use client";

/**
 * Reads the `?q=` the Workspace page's Ask Pivot entry hands over and starts the composer
 * with it. It lives apart from the workspace so the workspace keeps no router dependency.
 */

import { useSearchParams } from "next/navigation";
import { AssistantWorkspace } from "@/components/assistant/workspace";

export function AssistantWithHandover() {
  const question = useSearchParams().get("q") ?? undefined;
  // Keyed on the question so a second hand-over replaces the composer's draft.
  return <AssistantWorkspace key={question ?? ""} initialQuestion={question} />;
}
