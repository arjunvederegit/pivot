"use client";

/**
 * The two action panels on the Workspace page: the decisions a user can start, and — where
 * the build has it — the way into Ask Pivot. Both are plain entry points into implemented
 * workflows; neither describes how impressive a tool is, and neither shows data of its own
 * beyond counts the API already served.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { IconArrowRight } from "@/components/icons";

interface Decision {
  title: string;
  prompt: string;
  href: string;
  /** A served fact shown at the right edge ("3 saved"). */
  note?: string;
}

export function DecisionsPanel({
  teamId,
  savedDeals,
  contractsConfigured,
}: {
  teamId: string | null;
  /** Saved deals, when the list has loaded. */
  savedDeals: number | undefined;
  /** Whether contracts are imported; undefined while health loads. */
  contractsConfigured: boolean | undefined;
}) {
  const decisions: Decision[] = [
    {
      title: "Evaluate a trade",
      prompt: "Build a deal, check it against the rules, and read what it does to every side.",
      href: teamId ? `/trade-evaluator?team=${teamId}` : "/trade-evaluator",
    },
    {
      title: "Explore team needs",
      prompt: "Where the roster is short, and the acquisition targets that would address it.",
      href: teamId ? `/team-outlook/${teamId}` : "/team-outlook",
    },
    {
      title: "Compare players",
      prompt: "Put two to four players on the same league-percentile scale.",
      href: "/player-explorer",
    },
    {
      title: "Compare saved deals",
      prompt: "Rank the deals and strategies you have saved against each other.",
      href: "/strategy-lab",
      note: savedDeals === undefined ? undefined : savedDeals === 0 ? "none saved" : `${savedDeals} saved`,
    },
    {
      title: "Review cap position",
      prompt: "Payroll by season, expiring money and commitments.",
      href: "/salary-cap-center",
      note: contractsConfigured === false ? "contracts not imported" : undefined,
    },
  ];

  return (
    <section aria-labelledby="decisions-title" className="panel min-w-0">
      <header className="border-b border-hairline px-5 py-3.5">
        <h2 id="decisions-title" className="text-[1.05rem] font-semibold leading-tight text-foreground">
          Decisions
        </h2>
      </header>
      <ul className="divide-y divide-hairline">
        {decisions.map((d) => (
          <li key={d.title}>
            <Link
              href={d.href}
              className="group flex items-center gap-4 px-5 py-3 transition-colors hover:bg-panel2"
            >
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-semibold text-foreground">{d.title}</span>
                <span className="mt-0.5 block text-[13.5px] leading-snug text-muted">{d.prompt}</span>
              </span>
              {d.note && <span className="shrink-0 text-[12px] text-faint">{d.note}</span>}
              <IconArrowRight
                size={16}
                className="shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
              />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Ask Pivot's entry point. The question travels as `?q=` and lands in the assistant's
 * composer — it is not sent until the reader sends it there.
 */
export function AskPivotPanel() {
  const router = useRouter();
  const [question, setQuestion] = useState("");

  function open() {
    const q = question.trim();
    router.push(q ? `/assistant?q=${encodeURIComponent(q)}` : "/assistant");
  }

  return (
    <section aria-labelledby="ask-pivot-title" className="panel min-w-0">
      <header className="flex items-center justify-between gap-3 border-b border-hairline px-5 py-3.5">
        <h2 id="ask-pivot-title" className="text-[1.05rem] font-semibold leading-tight text-foreground">
          Ask Pivot
        </h2>
        <span className="rounded-[3px] border border-line px-1.5 py-px text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
          Lab · fictional data
        </span>
      </header>
      <form
        // Without JavaScript (or before hydration) the browser submits this as a plain GET to
        // the assistant, which reads the same `q`.
        action="/assistant"
        method="get"
        className="space-y-3 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault();
          open();
        }}
      >
        <p className="text-[12.5px] leading-snug text-muted">
          Ask about a player, a team, or where two scouting reports disagree. Answers cite the
          passages they used.
        </p>
        <label htmlFor="home-ask" className="sr-only">
          Ask Pivot a question
        </label>
        <div className="flex gap-2">
          <input
            id="home-ask"
            name="q"
            maxLength={2000}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ask a basketball question…"
            className="min-w-0 flex-1 rounded-md border border-line bg-arena px-3 py-2.5 text-[14px] text-foreground placeholder:text-faint focus:border-[#55555b]"
          />
          <button
            type="submit"
            className="shrink-0 rounded-md border border-volt bg-volt px-4 py-2.5 text-[14px] font-semibold text-ink transition-colors hover:bg-white"
          >
            Ask
          </button>
        </div>
      </form>
    </section>
  );
}
