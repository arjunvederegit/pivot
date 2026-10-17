/**
 * A card inside a Team Outlook section.
 *
 * The same surface as `Panel` from the shared UI, with one difference that matters to
 * assistive tech: its title is an h3. Team Outlook groups its cards under serif section
 * h2s, so a card title one level below keeps the outline honest — page, section, card.
 */

import clsx from "clsx";
import { useId } from "react";

export function TeamPanel({
  children,
  className,
  bodyClassName,
  title,
  subtitle,
  actions,
  padded = true,
  labelledBy,
}: {
  children?: React.ReactNode;
  className?: string;
  /** Classes for the body wrapper — e.g. to let it grow and pin a source rail. */
  bodyClassName?: string;
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  padded?: boolean;
  /** Id for the h3; one is generated when omitted. The card is named by its heading. */
  labelledBy?: string;
}) {
  const generated = useId();
  const headingId = title ? (labelledBy ?? generated) : undefined;
  return (
    <section className={clsx("panel min-w-0", className)} aria-labelledby={headingId}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-hairline px-4 py-4 sm:px-5">
          <div className="min-w-0">
            {title && (
              <h3 id={headingId} className="title-md text-[1.02rem] text-foreground">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-muted">{subtitle}</p>
            )}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx(padded && "p-4 sm:p-5", bodyClassName)}>{children}</div>
    </section>
  );
}
