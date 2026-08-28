/**
 * `/` — the Pivot narrative.
 *
 * Experimental. The whole implementation lives in `components/scroll-home/`; this file is
 * the only thing in `app/` that knows about it. To remove the experiment: delete that
 * directory, move `app/command-center/page.tsx` back to `app/page.tsx`, and restore `/`
 * at the head of `PRIMARY` in `components/shell.tsx`.
 */

import { ScrollHome } from "@/components/scroll-home/scroll-home";

export default function HomePage() {
  return <ScrollHome />;
}
