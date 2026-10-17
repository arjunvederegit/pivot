/**
 * The section heading the reference pages share (Cap Center, Data Health, About): a quiet
 * caps label over a serif h2 one step below the page's title-xl H1, with an optional note
 * on the right. Same shape as the home dashboard's section heads, so moving between the
 * dashboard and a reference page reads as one product.
 *
 * Server-safe: no hooks.
 */

import clsx from "clsx";

export function RefSectionHead({
  id,
  eyebrow,
  title,
  aside,
  className,
}: {
  /** The heading's id — what the section's `aria-labelledby` points at. */
  id: string;
  eyebrow: React.ReactNode;
  title: React.ReactNode;
  aside?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("mb-5 flex flex-wrap items-end justify-between gap-x-8 gap-y-2", className)}>
      <div className="min-w-0">
        <div className="eyebrow">{eyebrow}</div>
        <h2 id={id} className="title-lg mt-2 text-foreground">
          {title}
        </h2>
      </div>
      {aside && <div className="max-w-md text-[13px] leading-snug text-muted">{aside}</div>}
    </div>
  );
}
