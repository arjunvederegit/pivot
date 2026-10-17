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
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-8 gap-y-2">
      <div className="min-w-0">
        <div className="eyebrow flex items-center gap-2">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />
          {eyebrow}
        </div>
        <Tag id={id} className="title-lg mt-2 text-foreground">
          {title}
        </Tag>
      </div>
      {aside && <div className="max-w-md text-[13.5px] leading-snug text-muted">{aside}</div>}
    </div>
  );
}
