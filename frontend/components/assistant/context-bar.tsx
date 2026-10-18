"use client";

/**
 * The context a question is asked in: which fictional team, which mode, what the GM cares
 * about and what is off the table.
 *
 * All of it is the GM's *stated position* — sent with every question and echoed back in
 * each answer's "What the assistant was told" — and none of it is evidence. Choosing a team
 * loads that team's own default priorities and constraints (from its strategy memo in the
 * corpus) as a starting point the GM can then change.
 */

import { useId, useRef, useState } from "react";
import { possessive, priorityLevelLabel } from "@/lib/assistant-format";
import {
  MAX_CONSTRAINT_CHARS,
  MAX_CONSTRAINTS,
  PRIORITY_LEVELS,
  isPriorityLevel,
  teamDefaults,
  type AssistantContext,
  type AssistantMode,
  type AssistantTeam,
  type PriorityKey,
  type PriorityLevel,
  type Priorities,
} from "@/lib/assistant-types";
import { Button, SegmentedControl, Skeleton } from "@/components/ui";

const FIELD =
  "rounded-lg border border-line bg-panel2 px-2.5 py-1.5 text-[13px] text-foreground transition-colors placeholder:text-faint hover:border-[#3a433f] focus:border-signal/60 disabled:opacity-50";

const MODE_OPTIONS = [
  { value: "research", label: "Research" },
  { value: "roster_fit", label: "Roster fit" },
];

const MODE_HINT: Record<AssistantMode, string> = {
  research: "Open questions about fictional players, teams and scouting methods.",
  roster_fit:
    "Reads players against your priorities — documented observations side by side, never a fit score.",
};

function samePriorities(a: Priorities, b: Priorities): boolean {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => a[k as PriorityKey] === b[k as PriorityKey]);
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/* ----------------------------------------------------------------- priorities */

/** Strongest first, so a list of priorities reads like the GM's ranking. */
function byStrength(priorities: Priorities): [PriorityKey, PriorityLevel][] {
  const set = Object.entries(priorities) as [PriorityKey, PriorityLevel][];
  return set.sort((a, b) => PRIORITY_LEVELS.indexOf(a[1]) - PRIORITY_LEVELS.indexOf(b[1]));
}

function PriorityEditor({
  priorities,
  dimensions,
  fallbackTeam,
  onChange,
}: {
  priorities: Priorities;
  dimensions: AssistantContext["priority_dimensions"];
  /** Roster fit with a team: the backend uses this team's defaults when none are set. */
  fallbackTeam: AssistantTeam | null;
  onChange: (next: Priorities) => void;
}) {
  const labelOf = (key: string) => dimensions.find((d) => d.key === key)?.label ?? key;
  const set = byStrength(priorities);
  const available = dimensions.filter((d) => !(d.key in priorities));
  const fallback = fallbackTeam ? byStrength(teamDefaults(fallbackTeam).priorities) : [];

  return (
    <ul className="flex flex-wrap items-center gap-1.5" aria-label="Your priorities">
      {set.length === 0 && (
        <li className="text-[12.5px] text-faint">
          {fallbackTeam && fallback.length > 0
            ? `None set — the ${possessive(fallbackTeam.name)} documented defaults will be used: ${fallback
                .map(([key, level]) => `${labelOf(key)} · ${priorityLevelLabel(level)}`)
                .join("; ")}.`
            : "None set — the assistant reads the question alone."}
        </li>
      )}
      {set.map(([key, level]) => {
        const label = labelOf(key);
        const dimension = dimensions.find((d) => d.key === key);
        return (
          <li
            key={key}
            title={dimension?.description}
            className="inline-flex max-w-full items-center gap-1 rounded-lg border border-line bg-panel2 py-0.5 pl-2.5 pr-0.5 text-[12.5px]"
          >
            <span className="truncate text-foreground">{label}</span>
            <span aria-hidden className="text-faint">
              ·
            </span>
            <select
              aria-label={`${label} level`}
              value={level}
              onChange={(event) => {
                const next = { ...priorities };
                if (event.target.value === "remove") delete next[key];
                else if (isPriorityLevel(event.target.value)) next[key] = event.target.value;
                onChange(next);
              }}
              className={`cursor-pointer rounded-md bg-transparent py-1 pl-1 pr-1 text-[12.5px] font-medium hover:bg-panel3 focus:bg-panel3 ${
                level === "must_preserve" ? "text-signal" : "text-muted"
              }`}
            >
              {PRIORITY_LEVELS.map((l) => (
                <option key={l} value={l}>
                  {priorityLevelLabel(l)}
                </option>
              ))}
              <option value="remove">Remove</option>
            </select>
          </li>
        );
      })}
      {available.length > 0 && (
        <li>
          <select
            aria-label="Add a priority"
            value=""
            onChange={(event) => {
              const key = event.target.value as PriorityKey;
              if (key) onChange({ ...priorities, [key]: "medium" });
            }}
            className={`${FIELD} cursor-pointer border-dashed py-1 text-[12.5px] text-muted`}
          >
            <option value="" disabled>
              + Add priority
            </option>
            {available.map((d) => (
              <option key={d.key} value={d.key}>
                {d.label}
              </option>
            ))}
          </select>
        </li>
      )}
    </ul>
  );
}

/* ---------------------------------------------------------------- constraints */

function ConstraintEditor({
  constraints,
  fallbackTeam,
  onChange,
}: {
  constraints: string[];
  /** Roster fit with a team: the model is given this team's constraints when none are set. */
  fallbackTeam: AssistantTeam | null;
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const listRef = useRef<HTMLUListElement>(null);
  const filled = constraints.filter((c) => c.trim());
  const summary = filled.length
    ? `${filled.length} · ${filled[0]}${filled.length > 1 ? ` (+${filled.length - 1} more)` : ""}`
    : fallbackTeam && fallbackTeam.default_constraints.length > 0
      ? `None set — the ${possessive(fallbackTeam.name)} documented constraints will be used`
      : "None — add what is off the table (a timeline, a player you will not move…)";

  return (
    <div className="min-w-0">
      <div className="flex min-w-0 items-center gap-3">
        <span className="eyebrow shrink-0">Constraints</span>
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-muted" title={filled.join("\n")}>
          {summary}
        </span>
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen(!open)}
        >
          {open ? "Done" : "Edit"}
        </Button>
      </div>
      {open && (
        <div id={listId} className="mt-3 space-y-2">
          <ul ref={listRef} className="space-y-2">
            {constraints.map((c, i) => (
              <li key={i} className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <input
                    type="text"
                    value={c}
                    maxLength={MAX_CONSTRAINT_CHARS}
                    aria-label={`Constraint ${i + 1}`}
                    onChange={(event) => {
                      const next = [...constraints];
                      next[i] = event.target.value;
                      onChange(next);
                    }}
                    placeholder="e.g. Keep the closing five's spacing intact"
                    className={`${FIELD} w-full`}
                  />
                  {c.length > MAX_CONSTRAINT_CHARS - 40 && (
                    <span className="mt-0.5 block text-right text-[11px] tabular-nums text-faint">
                      {c.length} / {MAX_CONSTRAINT_CHARS}
                    </span>
                  )}
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove constraint ${i + 1}`}
                  onClick={() => onChange(constraints.filter((_, j) => j !== i))}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              variant="secondary"
              disabled={constraints.length >= MAX_CONSTRAINTS}
              onClick={() => {
                onChange([...constraints, ""]);
                requestAnimationFrame(() =>
                  listRef.current?.querySelector<HTMLInputElement>("li:last-child input")?.focus(),
                );
              }}
            >
              Add constraint
            </Button>
            <span className="text-[11.5px] tabular-nums text-faint">
              {constraints.length} / {MAX_CONSTRAINTS} · up to {MAX_CONSTRAINT_CHARS} characters each
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------ bar */

export function ContextBar({
  context,
  loading,
  error,
  onRetry,
  teamId,
  mode,
  priorities,
  constraints,
  onTeam,
  onMode,
  onPriorities,
  onConstraints,
}: {
  context: AssistantContext | undefined;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  teamId: string | null;
  mode: AssistantMode;
  priorities: Priorities;
  constraints: string[];
  onTeam: (team: AssistantTeam | null) => void;
  onMode: (mode: AssistantMode) => void;
  onPriorities: (next: Priorities) => void;
  onConstraints: (next: string[]) => void;
}) {
  const teamSelectId = useId();
  const team = context?.teams.find((t) => t.id === teamId) ?? null;

  if (loading) {
    return (
      <div className="panel space-y-3 p-4 sm:p-5" aria-label="Loading teams and priorities" role="status">
        <Skeleton className="h-9 w-full max-w-xl" />
        <Skeleton className="h-7 w-full max-w-2xl" />
      </div>
    );
  }
  if (!context) {
    return (
      <div className="panel flex flex-wrap items-center gap-3 p-4 text-[13px] text-muted sm:p-5">
        <span aria-hidden className="text-illegal">
          ✕
        </span>
        <span className="min-w-0 flex-1">
          Couldn’t load the fictional teams and priorities{error ? `: ${error}` : "."} You can
          still ask research questions without a team.
        </span>
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      </div>
    );
  }

  const defaults = team ? teamDefaults(team) : null;
  // Only roster fit falls back to a team's own position (backend `effective_priorities`).
  const fallbackTeam = mode === "roster_fit" ? team : null;
  const differsFromDefaults =
    defaults !== null &&
    (!samePriorities(priorities, defaults.priorities) || !sameList(constraints, defaults.constraints));

  return (
    <section aria-label="Question context" className="panel space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1 basis-64">
          <label htmlFor={teamSelectId} className="eyebrow mb-1.5 block">
            Fictional team
          </label>
          <select
            id={teamSelectId}
            value={team?.id ?? ""}
            onChange={(event) =>
              onTeam(context.teams.find((t) => t.id === event.target.value) ?? null)
            }
            className={`${FIELD} w-full max-w-sm cursor-pointer`}
          >
            <option value="">No team (research)</option>
            {context.teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          {team && (
            <p className="mt-1.5 max-w-xl text-[12px] leading-relaxed text-faint">{team.identity}</p>
          )}
        </div>
        <div className="min-w-0">
          <span className="eyebrow mb-1.5 block">Mode</span>
          <SegmentedControl
            ariaLabel="Mode"
            options={MODE_OPTIONS}
            value={mode}
            onChange={(v) => onMode(v as AssistantMode)}
          />
          <p className="mt-1.5 max-w-xs text-[12px] leading-relaxed text-faint">
            {mode === "roster_fit" && !team
              ? "Pick a team to read players against its priorities."
              : MODE_HINT[mode]}
          </p>
        </div>
      </div>

      <div className="border-t border-hairline pt-4">
        <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="eyebrow">{team ? `${possessive(team.name)} priorities` : "Your priorities"}</span>
          <span className="text-[11.5px] text-faint">Your stated position — sent with every question, never treated as evidence</span>
          {differsFromDefaults && team && (
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => onTeam(team)}>
              Reset to team defaults
            </Button>
          )}
        </div>
        <PriorityEditor
          priorities={priorities}
          dimensions={context.priority_dimensions}
          fallbackTeam={fallbackTeam}
          onChange={onPriorities}
        />
      </div>

      <div className="border-t border-hairline pt-3">
        <ConstraintEditor constraints={constraints} fallbackTeam={fallbackTeam} onChange={onConstraints} />
      </div>
    </section>
  );
}
