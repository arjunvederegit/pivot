/** A section heading on the trade pages: quiet caps eyebrow, serif title, optional aside —
 *  the same shape the home dashboard uses, one step below the page's H1. */
export function SectionHead({
  id,
  eyebrow,
  title,
  aside,
  as: Tag = "h2",
}: {
  id: string;
  eyebrow: React.ReactNode;
  title: React.ReactNode;
  aside?: React.ReactNode;
  as?: "h2" | "h3";
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-x-8 gap-y-2">
      <div className="min-w-0">
        <div className="eyebrow flex items-center gap-2 text-faint">{eyebrow}</div>
        <Tag id={id} className="title-lg mt-1.5 text-foreground">
          {title}
        </Tag>
      </div>
      {aside && <div className="max-w-md text-[13.5px] leading-snug text-muted">{aside}</div>}
    </div>
  );
}
