/**
 * A backend sentence whose `backtick` spans are shell commands ("Run `make sync-data` to
 * refresh from NBA.com."), drawn with each command as a code chip and no backticks.
 *
 * The sentence is the backend's own; only the code spans are restyled. The chip sizes
 * relative to the text around it (0.9em), so it reads at the same scale in a 12.5px body
 * line and a 12px status line, and never drops below 10px in either.
 */

import { CommandText } from "@/components/reference/format";

/* No `font-mono` class: `font-mono` now maps to the UI face, and a shell command is real
   code — the bare <code> element takes Geist Mono from the base layer. */
const CHIP =
  "whitespace-nowrap rounded-md border border-line bg-panel2 px-1.5 py-px text-[0.9em] text-foreground";

export function CommandLine({ text }: { text: string }) {
  return <CommandText text={text} codeClassName={CHIP} />;
}
