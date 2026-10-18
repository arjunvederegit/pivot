/**
 * Documentation primitives for the long-form reference pages (Methodology, About).
 *
 * Reading comes first: serif section headings, body copy at a comfortable measure, and
 * everything technical — formulas, validation numbers — folded into quiet cards a reader
 * opens on purpose. Server-safe: no hooks here (the contents rail and the formula's scroll
 * cue are their own client components).
 */

import clsx from "clsx";
import { ScrollCue } from "@/components/reference/scroll-cue";

export { DocToc, type DocTocItem } from "@/components/reference/doc-toc";

/**
 * The section heading of the long-form reference pages: title-lg's face one step smaller,
 * so a two- or three-line heading never rivals the page's title-xl H1. Shared so
 * Methodology and About match.
 */
export const DOC_HEADING =
  "title-lg text-[length:clamp(1.6rem,1.15rem+1.2vw,2.3rem)] text-balance text-foreground";

/** Body copy of a reference section: the reading measure and its inline emphasis. */
export const DOC_PROSE =
  "space-y-4 text-[15.5px] leading-[1.72] text-muted [&_em]:text-foreground/90 [&_strong]:font-semibold [&_strong]:text-foreground";

/** A numbered documentation section. `id` is the anchor other pages link to. */
export function DocSection({
  id,
  number,
  title,
  children,
  className,
}: {
  id: string;
  /** As printed in the contents ("03") — pass the TOC entry's own `number`. */
  number: string;
  title: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-heading`}
      className={clsx("scroll-mt-24 border-t border-hairline pt-10 first:border-t-0 first:pt-0", className)}
    >
      <div className="eyebrow numeral tracking-[0.06em] text-signal">{number}</div>
      {/* A dash stays on the line of the word before it, so a wrapped heading never opens a
          line with "— and what it can't know". */}
      <h2 id={`${id}-heading`} className={clsx(DOC_HEADING, "mt-2.5")}>
        {typeof title === "string" ? title.replace(/ —/g, " —") : title}
      </h2>
      <div className={clsx(DOC_PROSE, "mt-5")}>{children}</div>
    </section>
  );
}

/** A formula, set in a quiet inset card in the code face. */
export function DocFormula({ children, label = "Formula" }: { children: React.ReactNode; label?: string }) {
  return (
    <figure className="overflow-hidden rounded-lg border border-hairline bg-court">
      <figcaption className="border-b border-hairline px-4 py-2 text-[11.5px] font-medium text-faint">
        {label}
      </figcaption>
      <ScrollCue surface="ink">
        {/* No font utility: <pre> takes the code face (Geist Mono) from the base layer, and
            the multi-line formulas line up on its fixed advance. */}
        <pre className="w-max min-w-full px-4 py-3 text-[12.5px] leading-[1.7] text-foreground">{children}</pre>
      </ScrollCue>
    </figure>
  );
}

/** Expandable technical detail. Native `<details>`: keyboard-operable without JavaScript. */
export function DocTech({ children, summary = "Technical detail" }: { children: React.ReactNode; summary?: string }) {
  return (
    <details className="group rounded-xl border border-hairline bg-panel">
      <summary className="flex cursor-pointer select-none list-none items-center justify-between gap-3 rounded-xl px-4 py-3 transition-colors hover:bg-panel2 focus-visible:outline-offset-[-3px] [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2.5 text-[13.5px] font-medium text-foreground">
          <svg
            aria-hidden
            viewBox="0 0 16 16"
            className="h-3.5 w-3.5 shrink-0 text-faint transition-transform group-open:rotate-90"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m6 3.5 4.5 4.5L6 12.5" />
          </svg>
          {summary}
        </span>
        <span className="text-[12.5px] font-medium text-signal">
          <span className="group-open:hidden">Show</span>
          <span className="hidden group-open:inline">Hide</span>
        </span>
      </summary>
      <div className="space-y-3.5 border-t border-hairline px-4 py-4 text-[14.5px] leading-[1.7] text-muted [&_strong]:font-semibold [&_strong]:text-foreground">
        {children}
      </div>
    </details>
  );
}

/** A short definition list for "the four rules" style asides. */
export function DocPoints({ items }: { items: { term: React.ReactNode; body: React.ReactNode }[] }) {
  return (
    <dl className="grid gap-px overflow-hidden rounded-xl border border-hairline bg-hairline sm:grid-cols-2">
      {items.map((item, i) => (
        <div key={i} className="bg-panel px-4 py-3.5">
          <dt className="text-[14px] font-semibold text-foreground">{item.term}</dt>
          <dd className="mt-1 text-[14px] leading-relaxed text-muted">{item.body}</dd>
        </div>
      ))}
    </dl>
  );
}
