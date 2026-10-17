"use client";

/**
 * The contents of a long reference page.
 *
 * Wide screens: a sticky rail beside the text that marks the section being read, so a
 * reader deep in the rules check still knows where they are and can jump anywhere. Narrow
 * screens: a compact index card above the text (nothing sticks over the reading column on
 * a phone).
 *
 * The "you are here" mark is read from scroll position, not stored: the first render (on
 * the server and during hydration) marks nothing, and the mark arrives on the next frame.
 */

import clsx from "clsx";
import { useEffect, useState } from "react";

export interface DocTocItem {
  id: string;
  label: string;
  /** The section's own number as printed beside its heading ("03", "03.1"). Required so
   *  the contents can never number a section differently from the section itself. */
  number: string;
  /** A subsection: indented under its parent. */
  sub?: boolean;
}

/** Which section the reader is in: the last one whose top has passed 30% of the viewport. */
function useActiveSection(ids: string[]) {
  const [active, setActive] = useState<string | null>(null);
  const key = ids.join("|");

  useEffect(() => {
    const sections = key
      .split("|")
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (sections.length === 0) return;

    let frame = 0;
    const measure = () => {
      frame = 0;
      const line = window.innerHeight * 0.3;
      const atEnd =
        window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      let current: string | null = null;
      for (const section of sections) {
        if (section.getBoundingClientRect().top <= line) current = section.id;
      }
      // The last section is usually too short to ever reach the line; at the foot of the
      // page it is the one being read.
      if (atEnd) current = sections[sections.length - 1].id;
      setActive((prev) => (prev === current ? prev : current));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    schedule();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // Content that arrives late (the live impact reference) moves every section below it
    // without a scroll event, so a change in the page's height re-measures too.
    const observer = new ResizeObserver(schedule);
    observer.observe(document.body);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [key]);

  return active;
}

export function DocToc({ items, label = "On this page" }: { items: DocTocItem[]; label?: string }) {
  const active = useActiveSection(items.map((item) => item.id));

  return (
    <nav
      aria-label={label}
      className="min-w-0 rounded-xl border border-hairline bg-panel px-4 py-4 lg:rounded-none lg:border-0 lg:bg-transparent lg:p-0"
    >
      <div className="eyebrow mb-3">{label}</div>
      <ol className="grid gap-x-6 sm:grid-cols-2 lg:grid-cols-1 lg:border-l lg:border-hairline">
        {items.map((item) => {
          const current = item.id === active;
          return (
            <li key={item.id}>
              <a
                href={`#${item.id}`}
                aria-current={current ? "location" : undefined}
                className={clsx(
                  "group flex items-baseline gap-3 py-1.5 text-[13.5px] leading-snug transition-colors lg:-ml-px lg:border-l-2 lg:py-[7px] lg:pr-2",
                  item.sub ? "pl-5 lg:pl-8" : "lg:pl-4",
                  current
                    ? "text-foreground lg:border-signal"
                    : "text-muted hover:text-foreground lg:border-transparent lg:hover:border-line",
                )}
              >
                <span
                  className={clsx(
                    "numeral w-7 shrink-0 text-[11.5px] font-medium",
                    current ? "text-signal" : "text-faint group-hover:text-muted",
                  )}
                >
                  {item.number}
                </span>
                <span className="min-w-0">{item.label}</span>
              </a>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
