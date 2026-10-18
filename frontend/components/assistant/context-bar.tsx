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

import { useEffect, useId, useRef, useState } from "react";
import { possessive, priorityLevelLabel } from "@/lib/assistant-format";
import {
  MAX_CONSTRAINT_CHARS,
  MAX_CONSTRAINTS,
  PRIORITY_LEVELS,
  isPriorityLevel,
  teamDefaults,
  type AssistantContext,
  type AssistantMode,
  type Starter,
  type AssistantTeam,
  type PriorityKey,
  type PriorityLevel,
  type Priorities,
} from "@/lib/assistant-types";
import { Button, SegmentedControl, Skeleton } from "@/components/ui";
import { IconChevron, IconClose } from "@/components/icons";
import { InspectorDrawer } from "@/components/assistant/inspector";

const FIELD =
  "rounded-lg border border-line bg-panel2 px-2.5 py-1.5 text-[13px] text-foreground transition-colors placeholder:text-faint hover:border-[#4a4a50] focus:border-signal/60 disabled:opacity-50";

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

/* ------------------------------------------------------------- compact strip */

/**
 * The context controls, sized like a chat app's model selector: a row of small labelled chips
 * above the composer. Each chip opens a compact popover holding the SAME editor the old
 * always-open form held; on a narrow screen one "Context" chip opens all of them in a drawer.
 *
 * What a chip shows is what is applied: closing a popover changes nothing, and a constraint or
 * priority that will be sent is always counted on its chip. Nothing here adds a field or a
 * model switch — it is the existing team / mode / priorities / constraints context.
 */

type SectionKey = "team" | "mode" | "strategy" | "constraints" | "suggestions";

function Chip({
  label,
  value,
  open,
  onClick,
  id,
  panelId,
}: {
  label: string;
  value: string;
  open: boolean;
  onClick: () => void;
  id: string;
  panelId: string;
}) {
  return (
    <button
      type="button"
      id={id}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-controls={open ? panelId : undefined}
      aria-label={`${label}: ${value}`}
      onClick={onClick}
      className={`inline-flex max-w-[15rem] items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] transition-colors ${
        open ? "border-[#55555b] bg-panel3" : "border-line bg-transparent hover:bg-panel2"
      }`}
    >
      <span className="text-faint">{label}</span>
      <span className="truncate font-medium text-foreground">{value}</span>
      <IconChevron size={13} className="shrink-0 text-faint" />
    </button>
  );
}

export function ContextControls({
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
  starters,
  startersDisabled,
  onStarter,
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
  starters: Starter[];
  startersDisabled: boolean;
  onStarter: (prompt: string) => void;
}) {
  const uid = useId();
  const [open, setOpen] = useState<SectionKey | null>(null);
  const [drawer, setDrawer] = useState<{ opener: HTMLElement | null } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  // A popover closes on an outside press or Escape, and focus returns to its chip.
  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(null);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(null);
      openerRef.current?.focus();
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (loading) {
    return (
      <div role="status" aria-label="Loading teams and priorities" className="mb-3">
        <Skeleton className="h-8 w-full max-w-md" />
      </div>
    );
  }
  if (!context) {
    return (
      <div className="mb-3 flex flex-wrap items-center gap-3 text-[13px] text-muted">
        <span aria-hidden className="text-illegal">
          ✕
        </span>
        <span className="min-w-0 flex-1">
          Couldn’t load the fictional teams and priorities{error ? `: ${error}` : "."} You can still ask
          research questions without a team.
        </span>
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      </div>
    );
  }

  const team = context.teams.find((t) => t.id === teamId) ?? null;
  const filled = constraints.filter((c) => c.trim());
  const prioCount = Object.keys(priorities).length;
  const modeLabel = MODE_OPTIONS.find((m) => m.value === mode)?.label ?? mode;
  const fallbackTeam = mode === "roster_fit" ? team : null;
  const defaults = team ? teamDefaults(team) : null;
  const differsFromDefaults =
    defaults !== null &&
    (!samePriorities(priorities, defaults.priorities) || !sameList(constraints, defaults.constraints));
  const prioValue =
    prioCount > 0
      ? `${prioCount} set`
      : fallbackTeam && Object.keys(teamDefaults(fallbackTeam).priorities).length > 0
        ? "team defaults"
        : "none";
  const conValue =
    filled.length > 0
      ? `${filled.length} set`
      : fallbackTeam && fallbackTeam.default_constraints.length > 0
        ? "team defaults"
        : "none";

  const panelFor = (key: SectionKey) => `${uid}-${key}`;
  const toggle = (key: SectionKey, el: HTMLElement) => {
    openerRef.current = el;
    setOpen((cur) => (cur === key ? null : key));
  };

  const sections: Record<SectionKey, { title: string; node: React.ReactNode }> = {
    team: {
      title: "Fictional team",
      node: (
        <TeamSection
          context={context}
          team={team}
          onTeam={(t) => {
            onTeam(t);
          }}
        />
      ),
    },
    mode: { title: "Mode", node: <ModeSection mode={mode} team={team} onMode={onMode} /> },
    strategy: {
      title: team ? `${possessive(team.name)} priorities` : "Your priorities",
      node: (
        <div className="space-y-3">
          <p className="text-[12.5px] leading-snug text-faint">
            Your stated position — sent with every question, never treated as evidence.
          </p>
          <PriorityEditor
            priorities={priorities}
            dimensions={context.priority_dimensions}
            fallbackTeam={fallbackTeam}
            onChange={onPriorities}
          />
          {differsFromDefaults && team && (
            <Button size="sm" variant="ghost" onClick={() => onTeam(team)}>
              Reset to team defaults
            </Button>
          )}
        </div>
      ),
    },
    constraints: {
      title: "Constraints",
      node: <ConstraintEditor constraints={constraints} fallbackTeam={fallbackTeam} onChange={onConstraints} />,
    },
    suggestions: {
      title: "Suggestions",
      node: (
        <div className="space-y-2">
          <p className="text-[12.5px] leading-snug text-faint">
            Selecting a suggestion sends it, with the context shown on the chips.
          </p>
          {starters.length === 0 ? (
            <p className="text-[13px] text-muted">No suggestions for this team and mode.</p>
          ) : (
            <ul className="space-y-1.5">
              {starters.map((s) => (
                <li key={s.prompt}>
                  <button
                    type="button"
                    disabled={startersDisabled}
                    onClick={() => {
                      setOpen(null);
                      setDrawer(null);
                      onStarter(s.prompt);
                    }}
                    className="hover-frame flex w-full flex-col items-start gap-0.5 rounded-md border border-line bg-panel2/70 px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <span className="text-[13.5px] leading-snug text-foreground [overflow-wrap:anywhere]">{s.prompt}</span>
                    {s.intent && <span className="text-[11.5px] leading-snug text-faint">{s.intent}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ),
    },
  };

  const chips: { key: SectionKey; label: string; value: string }[] = [
    { key: "team", label: "Team", value: team?.name ?? "None" },
    { key: "mode", label: "Mode", value: modeLabel },
    { key: "strategy", label: "Strategy", value: prioValue },
    { key: "constraints", label: "Constraints", value: conValue },
    { key: "suggestions", label: "Suggestions", value: String(starters.length) },
  ];

  return (
    <div ref={boxRef} aria-label="Question context" role="group" className="mb-2.5">
      {/* Wide screens: one chip per control, each opening its own compact popover. */}
      <div className="hidden flex-wrap items-center gap-1.5 md:flex">
        {chips.map((c) => (
          <div key={c.key} className="relative">
            <Chip
              id={`${uid}-chip-${c.key}`}
              panelId={panelFor(c.key)}
              label={c.label}
              value={c.value}
              open={open === c.key}
              onClick={() => {
                const el = document.getElementById(`${uid}-chip-${c.key}`);
                if (el) toggle(c.key, el);
              }}
            />
            {open === c.key && (
              <div
                id={panelFor(c.key)}
                role="dialog"
                aria-label={`${c.label} settings`}
                className="fade-in absolute bottom-full left-0 z-40 mb-2 w-[min(92vw,26rem)] rounded-md border border-line bg-arena p-4 shadow-[var(--shadow-pop)]"
              >
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h3 className="text-[13.5px] font-semibold text-foreground">{sections[c.key].title}</h3>
                  <button
                    type="button"
                    aria-label={`Close ${sections[c.key].title}`}
                    onClick={() => {
                      setOpen(null);
                      openerRef.current?.focus();
                    }}
                    className="grid h-7 w-7 place-items-center rounded-md text-muted hover:bg-panel2 hover:text-foreground"
                  >
                    <IconClose size={15} />
                  </button>
                </div>
                <div className="scroll-thin max-h-[22rem] overflow-y-auto pr-0.5">{sections[c.key].node}</div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Narrow screens: one Context chip, all of it in a drawer. */}
      <div className="md:hidden">
        <button
          type="button"
          aria-haspopup="dialog"
          onClick={(e) => setDrawer({ opener: e.currentTarget })}
          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-[12.5px] hover:bg-panel2"
        >
          <span className="text-faint">Context</span>
          <span className="truncate font-medium text-foreground">
            {team?.name ?? "No team"} · {modeLabel} · {prioCount + filled.length} set
          </span>
          <IconChevron size={13} className="shrink-0 text-faint" />
        </button>
      </div>
      {drawer && (
        <InspectorDrawer
          label="Context"
          closeLabel="Close context"
          onClose={() => setDrawer(null)}
          returnFocusTo={drawer.opener}
          drawerId="assistant-context-drawer"
          showFrom="md"
        >
          <div className="space-y-5">
            {(["team", "mode", "strategy", "constraints", "suggestions"] as SectionKey[]).map((k) => (
              <section key={k} aria-label={sections[k].title}>
                <h3 className="eyebrow mb-2 text-foreground">{sections[k].title}</h3>
                {sections[k].node}
              </section>
            ))}
          </div>
        </InspectorDrawer>
      )}
    </div>
  );
}

function TeamSection({
  context,
  team,
  onTeam,
}: {
  context: AssistantContext;
  team: AssistantTeam | null;
  onTeam: (team: AssistantTeam | null) => void;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="sr-only">
        Fictional team
      </label>
      <select
        id={id}
        value={team?.id ?? ""}
        onChange={(event) => onTeam(context.teams.find((t) => t.id === event.target.value) ?? null)}
        className={`${FIELD} w-full cursor-pointer`}
      >
        <option value="">No team (research)</option>
        {context.teams.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      {team && <p className="mt-2 text-[12.5px] leading-relaxed text-faint">{team.identity}</p>}
      <p className="mt-2 text-[12px] leading-snug text-faint">
        A fictional team for this sandbox — not the NBA team selected elsewhere in Pivot.
      </p>
    </div>
  );
}

function ModeSection({
  mode,
  team,
  onMode,
}: {
  mode: AssistantMode;
  team: AssistantTeam | null;
  onMode: (mode: AssistantMode) => void;
}) {
  return (
    <div>
      <SegmentedControl ariaLabel="Mode" options={MODE_OPTIONS} value={mode} onChange={(v) => onMode(v as AssistantMode)} />
      <p className="mt-2 text-[12.5px] leading-relaxed text-faint">
        {mode === "roster_fit" && !team ? "Pick a team to read players against its priorities." : MODE_HINT[mode]}
      </p>
    </div>
  );
}
