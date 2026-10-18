"use client";

/**
 * Pivot interface primitives.
 *
 * Three rules run through everything here:
 *  - Status is never carried by colour alone — every status badge also carries a
 *    glyph and a word.
 *  - Cards are quiet charcoal surfaces with a hairline frame; in team context a thin
 *    team-coloured line runs along the top edge.
 *  - Headlines speak in the editorial serif; everything a person operates or reads as
 *    data is set in the UI sans with tabular figures.
 */

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import { formatDate } from "@/lib/format";

/* ------------------------------------------------------------------ surface */

export function Panel({
  children,
  className,
  title,
  subtitle,
  actions,
  accent,
  as: Tag = "section",
  padded = true,
}: {
  children?: React.ReactNode;
  className?: string;
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  /** Top-edge tab colour — pass a team colour inside team context. */
  accent?: string;
  as?: "section" | "div" | "article" | "aside";
  padded?: boolean;
}) {
  return (
    <Tag
      className={clsx("panel", className)}
      style={accent ? ({ "--edge": accent } as React.CSSProperties) : undefined}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-hairline px-5 py-3.5">
          <div className="min-w-0">
            {title && <h2 className="title-md truncate text-foreground">{title}</h2>}
            {subtitle && (
              <div className="mt-1 text-[13.5px] leading-relaxed text-muted">{subtitle}</div>
            )}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={padded ? "p-5" : undefined}>{children}</div>
    </Tag>
  );
}

/** Kept for source compatibility with pages written before the redesign. */
export const Card = Panel;

/* ------------------------------------------------------------------- header */

export function PageHeader({
  eyebrow,
  title,
  lede,
  actions,
  meta,
  accent,
}: {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  lede?: React.ReactNode;
  actions?: React.ReactNode;
  meta?: React.ReactNode;
  accent?: string;
}) {
  return (
    <header className="relative mb-6 lg:mb-7">
      <div className="relative flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          {eyebrow && (
            <div className="eyebrow mb-2.5 flex items-center gap-2 text-faint">
              {accent && (
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: accent }}
                />
              )}
              {eyebrow}
            </div>
          )}
          {/* Not `whitespace-nowrap`: a heading that cannot wrap forces the whole page to
              scroll sideways at 375px. `text-balance` keeps it tidy where it wraps. */}
          {/* One step above the title-lg section headings pages use under it. */}
          <h1 className="title-xl text-balance text-foreground">{title}</h1>
          {lede && (
            <p className="mt-2.5 max-w-3xl text-[15.5px] leading-relaxed text-muted">{lede}</p>
          )}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {meta && <div className="relative mt-3.5 flex flex-wrap items-center gap-2">{meta}</div>}
    </header>
  );
}

/** A section heading inside a page: quiet caps label, hairline rule, optional right slot. */
export function SectionLabel({
  children,
  aside,
  className,
}: {
  children: React.ReactNode;
  aside?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("mb-3 flex items-center gap-3", className)}>
      <span className="eyebrow shrink-0 text-foreground">{children}</span>
      <span aria-hidden className="h-px min-w-6 flex-1 bg-line" />
      {aside && <span className="shrink-0">{aside}</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ actions */

const BUTTON_VARIANTS: Record<string, string> = {
  primary: "bg-volt text-ink font-semibold border border-volt hover:bg-white active:bg-volt2",
  signal: "bg-volt text-ink font-semibold border border-volt hover:bg-white active:bg-volt2",
  secondary: "border border-line bg-transparent text-foreground hover:bg-panel2 hover:border-[#55555b]",
  ghost: "border border-transparent text-muted hover:bg-panel2 hover:text-foreground",
  danger: "border border-illegal/45 bg-illegal/10 text-illegal hover:bg-illegal/20",
};

type ButtonProps = {
  variant?: keyof typeof BUTTON_VARIANTS;
  size?: "sm" | "md";
  className?: string;
  children: React.ReactNode;
};

function buttonClass(variant: string, size: string, className?: string) {
  return clsx(
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md transition-[background-color,border-color,color] duration-150 disabled:cursor-not-allowed disabled:opacity-40",
    size === "sm" ? "px-3 py-1.5 text-[13px]" : "px-4 py-2.5 text-[14px]",
    BUTTON_VARIANTS[variant] ?? BUTTON_VARIANTS.secondary,
    className,
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: ButtonProps &
  React.ButtonHTMLAttributes<HTMLButtonElement> & {
    /** React 19 passes `ref` as a prop; it reaches the <button> through `rest`. */
    ref?: React.Ref<HTMLButtonElement>;
  }) {
  return (
    <button className={buttonClass(variant, size, className)} {...rest}>
      {children}
    </button>
  );
}

export function ButtonLink({
  variant = "secondary",
  size = "md",
  className,
  children,
  href,
  ...rest
}: ButtonProps & { href: string } & Omit<
    React.AnchorHTMLAttributes<HTMLAnchorElement>,
    "href" | "className" | "children"
  >) {
  return (
    <Link href={href} className={buttonClass(variant, size, className)} {...rest}>
      {children}
    </Link>
  );
}

/* ------------------------------------------------------------------- status */

interface StatusStyle {
  className: string;
  glyph: string;
}

const STATUS: Record<string, StatusStyle> = {
  pass: { className: "border-legal/45 bg-legal/10 text-legal", glyph: "✓" },
  verified_legal: { className: "border-legal/45 bg-legal/10 text-legal", glyph: "✓" },
  succeeded: { className: "border-legal/45 bg-legal/10 text-legal", glyph: "✓" },
  fresh: { className: "border-legal/45 bg-legal/10 text-legal", glyph: "✓" },
  fail: { className: "border-illegal/45 bg-illegal/10 text-illegal", glyph: "✕" },
  verified_illegal: { className: "border-illegal/45 bg-illegal/10 text-illegal", glyph: "✕" },
  failed: { className: "border-illegal/45 bg-illegal/10 text-illegal", glyph: "✕" },
  error: { className: "border-illegal/45 bg-illegal/10 text-illegal", glyph: "✕" },
  warning: { className: "border-conditional/45 bg-conditional/10 text-conditional", glyph: "!" },
  conditionally_valid: {
    className: "border-conditional/45 bg-conditional/10 text-conditional",
    glyph: "~",
  },
  stale: { className: "border-conditional/45 bg-conditional/10 text-conditional", glyph: "!" },
  incomplete: { className: "border-conditional/45 bg-conditional/10 text-conditional", glyph: "~" },
  unavailable: { className: "border-unavail/45 bg-unavail/10 text-unavail", glyph: "—" },
  not_evaluated: { className: "border-unavail/45 bg-unavail/10 text-unavail", glyph: "—" },
  derived: { className: "border-foreground/25 bg-foreground/5 text-foreground", glyph: "≈" },
  info: { className: "border-foreground/25 bg-foreground/5 text-foreground", glyph: "•" },
  running: { className: "border-signal/45 bg-signal/10 text-signal", glyph: "•" },
};

export function Badge({
  status,
  children,
  className,
  glyph = true,
}: {
  status?: string;
  children: React.ReactNode;
  className?: string;
  glyph?: boolean;
}) {
  const style = STATUS[status ?? "info"] ?? STATUS.info;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-[4px] border px-2 py-[3px] text-[11px] font-medium leading-tight",
        style.className,
        className,
      )}
    >
      {glyph && (
        <span aria-hidden className="text-[10px] leading-none opacity-90">
          {style.glyph}
        </span>
      )}
      {children}
    </span>
  );
}

/* --------------------------------------------------------------- scoreboard */

/** A single instrument reading: quiet label, loud number, optional note. */
export function StatBlock({
  label,
  value,
  note,
  accent,
  align = "left",
  size = "md",
  title,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  note?: React.ReactNode;
  accent?: string;
  align?: "left" | "center" | "right";
  size?: "sm" | "md" | "lg";
  title?: string;
}) {
  return (
    <div
      className={clsx(
        align === "center" && "text-center",
        align === "right" && "text-right",
        "min-w-0",
      )}
      title={title}
    >
      <div className="eyebrow truncate">{label}</div>
      <div
        className={clsx(
          "mt-2 leading-none",
          size === "sm" && "numeral text-[1.2rem]",
          size === "md" && "stat-serif text-[2.1rem]",
          size === "lg" && "stat-serif text-[2.9rem]",
        )}
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </div>
      {note && <div className="mt-2 text-[12px] leading-snug text-muted">{note}</div>}
    </div>
  );
}

/** Provenance rail — the structural device that carries the honesty standard.
 *
 * `source` is required and has no default. It used to default to "NBA.com via nba_api",
 * which meant any rail rendered without the prop asserted NBA.com regardless of where the
 * data came from — the same defect the API had. Pass what the endpoint served.
 */
export function SourceRail({
  source,
  retrievedAt,
  extra,
  className,
}: {
  source: string;
  retrievedAt?: string | null;
  extra?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-hairline pt-3 text-[11.5px] text-faint",
        className,
      )}
    >
      <span className="font-medium text-muted">Source</span>
      <span className="text-muted">{source}</span>
      {retrievedAt !== undefined && (
        <>
          <span aria-hidden>·</span>
          <span>updated {formatDate(retrievedAt)}</span>
        </>
      )}
      {extra}
    </div>
  );
}

export function SourceLine({
  source,
  retrievedAt,
  className,
}: {
  source: string;
  retrievedAt?: string | null;
  className?: string;
}) {
  return (
    <p className={clsx("text-[11.5px] text-faint", className)}>
      Source: {source} · Updated {formatDate(retrievedAt)}
    </p>
  );
}

export function FreshnessBadge({
  retrievedAt,
  staleAfterHours = 24,
}: {
  retrievedAt: string | null | undefined;
  staleAfterHours?: number;
}) {
  const [now] = useState(() => Date.now());
  if (!retrievedAt) return <Badge status="unavailable">no data</Badge>;
  const ageHours = (now - new Date(retrievedAt).getTime()) / 3_600_000;
  if (ageHours > staleAfterHours)
    return <Badge status="stale">stale · {Math.round(ageHours)}h old</Badge>;
  return <Badge status="pass">fresh · {formatDate(retrievedAt)}</Badge>;
}

/* ---------------------------------------------------------------- feedback */

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-[13px] text-muted" role="status">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-signal" />
      {label}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} aria-hidden />;
}

export function SkeletonRows({ rows = 5, height = "h-11" }: { rows?: number; height?: string }) {
  return (
    <div className="space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className={height} />
      ))}
    </div>
  );
}

export function ErrorState({ message, action }: { message: string; action?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-illegal/40 bg-illegal/8 px-4 py-3">
      <p className="flex items-start gap-2 text-sm text-illegal">
        <span aria-hidden className="text-[12px] leading-5">
          ✕
        </span>
        <span>{message}</span>
      </p>
      {action && <div className="mt-2.5 pl-5">{action}</div>}
    </div>
  );
}

/** An empty screen is an invitation to act, so it always offers the next step. */
export function EmptyState({
  title,
  hint,
  action,
  className,
}: {
  title: string;
  hint?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "relative overflow-hidden rounded-md border border-dashed border-line bg-panel px-6 py-12 text-center",
        className,
      )}
    >
      <svg
        aria-hidden
        viewBox="0 0 120 40"
        className="mx-auto mb-3 h-7 w-24 text-faint"
        fill="none"
      >
        <path d="M4 38h112M40 38V14h40v24M30 38a30 30 0 0 1 60 0" stroke="currentColor" strokeWidth="1.3" />
        <circle cx="60" cy="30" r="3" stroke="currentColor" strokeWidth="1.3" />
      </svg>
      <p className="font-serif text-[1.35rem] leading-tight text-foreground">{title}</p>
      {hint && <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-muted">{hint}</p>}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

export function UnavailableNotice({
  reason,
  steps,
}: {
  reason: React.ReactNode;
  steps?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-unavail/50 bg-unavail/6 px-4 py-3">
      <p className="flex items-start gap-2 text-sm text-muted">
        <span aria-hidden className="mt-0.5 text-unavail">
          —
        </span>
        <span>
          <span className="font-semibold text-foreground">Not available. </span>
          {reason}
        </span>
      </p>
      {steps && <div className="mt-2.5 border-t border-hairline pt-2.5">{steps}</div>}
    </div>
  );
}

/** Click-to-reveal explanation. Native `<details>`, so it works with a keyboard and
 *  without JavaScript; the summary is the only thing a reader must see. */
export function Explainer({
  summary,
  children,
  className,
}: {
  summary: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <details className={clsx("group", className)}>
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12px] font-medium text-muted transition-colors hover:text-foreground">
        <span
          aria-hidden
          className="inline-block w-3 text-center transition-transform group-open:rotate-90"
        >
          ›
        </span>
        {summary}
      </summary>
      <div className="mt-2 pl-[18px] text-[12px] leading-relaxed text-muted">{children}</div>
    </details>
  );
}

/* -------------------------------------------------------------- navigation */

export function Tabs({
  tabs,
  active,
  onChange,
  ariaLabel,
}: {
  tabs: { id: string; label: string }[];
  active: string;
  onChange: (id: string) => void;
  ariaLabel: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="scroll-thin -mx-1 flex gap-0.5 overflow-x-auto border-b border-hairline px-1"
    >
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            role="tab"
            type="button"
            aria-selected={selected}
            onClick={() => onChange(tab.id)}
            // A partly visible tab is not scrolled into view on focus by every browser,
            // so keyboard users could land on a tab hidden past the strip's edge.
            onFocus={(event) =>
              event.currentTarget.scrollIntoView({ block: "nearest", inline: "nearest" })
            }
            className={clsx(
              // The strip scrolls sideways, which clips anything drawn outside a tab, so the
              // focus ring is drawn inside it.
              "-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-[13.5px] transition-colors focus-visible:outline-offset-[-3px]",
              selected
                ? "border-signal font-medium text-foreground"
                : "border-transparent text-muted hover:text-foreground",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

export function SegmentedControl({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="inline-flex rounded-md border border-hairline bg-panel2 p-0.5"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          onClick={() => onChange(option.value)}
          className={clsx(
            "whitespace-nowrap rounded-[4px] px-3 py-1.5 text-[12.5px] transition-colors",
            value === option.value
              ? "bg-foreground font-medium text-ink"
              : "text-muted hover:text-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ atoms */

export function MeterBar({
  value,
  max = 1,
  color = "var(--signal)",
  className,
  label,
}: {
  value: number;
  max?: number;
  color?: string;
  className?: string;
  label?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div
      className={clsx("h-1.5 w-full overflow-hidden rounded-full bg-panel3", className)}
      role={label ? "img" : undefined}
      aria-label={label}
    >
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  );
}

export function PlayerAvatar({ name, size = 32 }: { name: string; size?: number }) {
  const initials = name
    .split(" ")
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("");
  return (
    <span
      aria-hidden
      className="numeral flex items-center justify-center rounded-full bg-panel3 text-muted"
      style={{ width: size, height: size, fontSize: size * 0.36 }}
    >
      {initials}
    </span>
  );
}

export function Th({
  children,
  className,
  numeric,
}: {
  children?: React.ReactNode;
  className?: string;
  numeric?: boolean;
}) {
  return (
    <th
      scope="col"
      className={clsx(
        "eyebrow whitespace-nowrap px-2 py-2.5",
        numeric ? "text-right" : "text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className,
  numeric,
}: {
  children?: React.ReactNode;
  className?: string;
  numeric?: boolean;
}) {
  return (
    <td className={clsx("px-2 py-1.5 text-sm", numeric && "data text-right", className)}>
      {children}
    </td>
  );
}
