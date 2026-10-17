/**
 * The section heading Team Outlook uses between its groups of panels: a quiet caps
 * eyebrow, the serif `title-lg` h2 one step below the page's `title-xl` H1, and an
 * optional note on the right. The same shape as the home dashboard's section heads, so
 * the two pages read as one publication.
 */

import clsx from "clsx";

export function SectionHead({
  id,
  eyebrow,
  title,
  aside,
  className,
}: {
  id: string;
  eyebrow: React.ReactNode;
  title: React.ReactNode;
  aside?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "mb-4 flex flex-wrap items-end justify-between gap-x-8 gap-y-2 lg:mb-5",
        className,
      )}
    >
      <div className="min-w-0">
        <div className="eyebrow">{eyebrow}</div>
        <h2 id={id} className="title-lg mt-2 text-foreground">
          {title}
        </h2>
      </div>
      {aside && (
        <div className="max-w-md text-[13px] leading-relaxed text-muted lg:text-balance lg:text-right">{aside}</div>
      )}
    </div>
  );
}
