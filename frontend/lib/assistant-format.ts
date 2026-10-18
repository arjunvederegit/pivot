/**
 * A deliberately tiny, safe Markdown reader for assistant answers.
 *
 * The local model writes light Markdown — paragraphs, `-`/`*`/`1.` lists, `**bold**`, a
 * `### Heading` or a `**Label**` line — and cites passages inline as `[S03]` or
 * `[S03, S05]`. This module turns that text into a small block/inline tree. It never
 * produces HTML: `components/assistant/answer-body.tsx` renders the tree as React elements,
 * and every piece of text reaches the DOM as a text node, so `<img onerror=…>` in an answer
 * (or in a retrieved, possibly hostile, document the model echoed) is shown as the
 * characters it is. There is no `dangerouslySetInnerHTML` anywhere on this path, and no
 * links: a URL in an answer stays inert text.
 *
 * What is recognised, and nothing else:
 *   blocks   paragraph · list (ordered/unordered, flat) · heading (`#`–`######`, or a line
 *            that is only `**Label**` / `**Label:**`) · a thematic break just separates
 *   inlines  text · line break · `**strong**` · citation
 *
 * **Citations are parsed exactly as the backend parses them** (`claims.py`:
 * `_CITATION_BLOCK` / `normalise_source_id` / `_MENTION_BEFORE`), so the chip a reader clicks
 * and the id the citation checker validated are the same id: `[S3]` and `[s003]` both mean
 * `S03`, and a block may separate ids with `,` `;` `/` or `&`. An id only *named* after a
 * verb of citing is reported speech, not a citation. Anything else in brackets is text.
 */

export type Inline =
  | { type: "text"; text: string }
  | { type: "break" }
  | { type: "strong"; children: Inline[] }
  | { type: "citation"; ids: string[]; raw: string }
  /** A sentence the backend's answer guard removed, replaced by its reason. */
  | { type: "removed"; reason: string };

export type Block =
  | { type: "paragraph"; inlines: Inline[] }
  | { type: "heading"; inlines: Inline[] }
  | { type: "list"; ordered: boolean; start: number; items: Inline[][] };

/* ---------------------------------------------------------------- citations */

// Mirrors backend/app/assistant_rag/claims.py. A factory, not a shared /g regex: a global
// RegExp carries `lastIndex` between calls, which is a classic source of skipped matches.
// Brackets or parentheses: the local model writes "(S03)" about as often as "[S03]".
const citationBlock = () => /[[(]\s*(S\d{1,3}(?:\s*[,;/&]\s*S\d{1,3})*)\s*[\])]/gi;
const citationId = () => /S(\d{1,3})/gi;

/** `S3`, `s03`, `S003` → `S03`. Mirrors the backend's `f"S{int(n):02d}"`. */
export function normaliseSourceId(raw: string): string {
  const match = /^\s*S(\d{1,3})\s*$/i.exec(raw);
  if (!match) return raw.trim().toUpperCase();
  return `S${String(Number.parseInt(match[1], 10)).padStart(2, "0")}`;
}

function idsInBlock(inner: string): string[] {
  const ids: string[] = [];
  for (const m of inner.matchAll(citationId())) {
    const id = normaliseSourceId(m[0]);
    if (!ids.includes(id)) ids.push(id);
  }
  return ids;
}

// Mirrors claims._MENTION_BEFORE, both alternatives: an id *named* after a verb of citing
// ("asked the assistant to cite source [S99]", "cite the planted source [S99]") is reported
// speech, not a citation, and renders as text. Python's `\w` and `\b` are Unicode-aware, so
// they are spelled out here as `[\p{L}\p{N}_]` (JavaScript's `\w` is ASCII-only).
const MENTION_BEFORE =
  /(?<![\p{L}\p{N}_])cit(?:e|es|ed|ing)\s+(?:it\s+|this\s+|them\s+)?(?:as\s+)?(?:a\s+)?(?:fake\s+|nonexistent\s+)?(?:source\s+)?(?:id\s+)?$|(?<![\p{L}\p{N}_])cit(?:e|es|ed|ing)\s+(?:an?\s+|the\s+)?(?:[\p{L}\p{N}_-]+\s+){0,2}(?:source|id|reference)\s+$/iu;

/** `claims._is_mention`: the 40 characters (code points, as Python slices) before `at`. */
function isMention(text: string, at: number): boolean {
  const window = Array.from(text.slice(Math.max(0, at - 80), at)).slice(-40).join("");
  return MENTION_BEFORE.test(window);
}

/** Every cited id, in order of first appearance, de-duplicated (as the backend reports it). */
export function extractCitationIds(text: string): string[] {
  const seen: string[] = [];
  for (const block of text.matchAll(citationBlock())) {
    if (isMention(text, block.index ?? 0)) continue;
    for (const id of idsInBlock(block[1])) if (!seen.includes(id)) seen.push(id);
  }
  return seen;
}

/* ------------------------------------------------------------------ inlines */

function textWithBreaks(text: string, out: Inline[]) {
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    if (i > 0) out.push({ type: "break" });
    if (line) out.push({ type: "text", text: line });
  });
}

// A run between `**` spans or guard markers is checked on its own: the backend's pattern
// cannot match across `*` or `⟩` either, so the answer is the same as on the whole line.
function citationsOnly(text: string, out: Inline[]) {
  let cursor = 0;
  for (const m of text.matchAll(citationBlock())) {
    const at = m.index ?? 0;
    if (isMention(text, at)) continue;
    if (at > cursor) textWithBreaks(text.slice(cursor, at), out);
    out.push({ type: "citation", ids: idsInBlock(m[1]), raw: m[0] });
    cursor = at + m[0].length;
  }
  if (cursor < text.length) textWithBreaks(text.slice(cursor), out);
}

// Mirrors guard.MARKER_PREFIX / MARKER_SUFFIX in the backend.
const REMOVED_MARKER = () => /⟨Removed by Ask Pivot’s checks: ([^⟩]*)⟩|⟨Removed by Ask Pivot's checks: ([^⟩]*)⟩/g;

const markerReason = (m: RegExpMatchArray) => (m[1] ?? m[2] ?? "").replace(/\.$/, "");

/** A text run, or a sentence the backend's answer guard removed (its reason in place). */
export type MarkedText = { type: "text"; text: string } | { type: "removed"; reason: string };

/** Split guard markers out of plain text, in order. Text without a marker is one run. */
export function splitRemovedMarkers(text: string): MarkedText[] {
  const out: MarkedText[] = [];
  let cursor = 0;
  for (const m of text.matchAll(REMOVED_MARKER())) {
    const at = m.index ?? 0;
    if (at > cursor) out.push({ type: "text", text: text.slice(cursor, at) });
    out.push({ type: "removed", reason: markerReason(m) });
    cursor = at + m[0].length;
  }
  if (cursor < text.length) out.push({ type: "text", text: text.slice(cursor) });
  return out;
}

/** True when `text` is nothing but guard markers — a removed sentence, not a statement. */
export function isRemovedMarker(text: string): boolean {
  const parts = splitRemovedMarkers(text);
  return parts.some((p) => p.type === "removed") && parts.every((p) => p.type === "removed" || !p.text.trim());
}

function citationsIn(text: string): Inline[] {
  const out: Inline[] = [];
  let cursor = 0;
  for (const m of text.matchAll(REMOVED_MARKER())) {
    const at = m.index ?? 0;
    if (at > cursor) citationsOnly(text.slice(cursor, at), out);
    out.push({ type: "removed", reason: markerReason(m) });
    cursor = at + m[0].length;
  }
  if (cursor < text.length) citationsOnly(text.slice(cursor), out);
  return out;
}

/**
 * Inline content: `**strong**` spans (not nested), citations, and line breaks.
 * An unclosed `**` is left as literal asterisks — never guessed at.
 */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const strong = /\*\*(?=\S)([\s\S]*?\S)\*\*/g;
  let cursor = 0;
  for (const m of text.matchAll(strong)) {
    const at = m.index ?? 0;
    if (at > cursor) out.push(...citationsIn(text.slice(cursor, at)));
    out.push({ type: "strong", children: citationsIn(m[1]) });
    cursor = at + m[0].length;
  }
  if (cursor < text.length) out.push(...citationsIn(text.slice(cursor)));
  return out;
}

/* ------------------------------------------------------------------- blocks */

const HEADING = /^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$/;
const LABEL_LINE = /^\s*\*\*([^*\n]+?)\*\*\s*:?\s*$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*(\d{1,3})[.)]\s+(.*)$/;
const THEMATIC_BREAK = /^\s*([-*_])(\s*\1){2,}\s*$/;

/** Parse an answer into blocks. Pure; safe on any input, including the empty string. */
export function parseAnswer(text: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; start: number; items: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ type: "paragraph", inlines: parseInline(paragraph.join("\n")) });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push({
        type: "list",
        ordered: list.ordered,
        start: list.start,
        items: list.items.map((item) => parseInline(item)),
      });
      list = null;
    }
  };

  for (const rawLine of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = rawLine.replace(/\s+$/, "");

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    if (THEMATIC_BREAK.test(line)) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = HEADING.exec(line) ?? LABEL_LINE.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      const label = heading[1].replace(/:\s*$/, "").trim();
      if (label) blocks.push({ type: "heading", inlines: parseInline(label) });
      continue;
    }

    const bullet = BULLET.exec(line);
    const ordered = bullet ? null : ORDERED.exec(line);
    if (bullet || ordered) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      const content = bullet ? bullet[1] : (ordered as RegExpExecArray)[2];
      if (!list || list.ordered !== isOrdered) {
        flushList();
        list = {
          ordered: isOrdered,
          start: ordered ? Number.parseInt(ordered[1], 10) : 1,
          items: [],
        };
      }
      list.items.push(content);
      continue;
    }

    // An indented line straight after a list item continues that item.
    if (list && /^\s{2,}\S/.test(rawLine)) {
      const items: string[] = list.items;
      items[items.length - 1] += `\n${line.trim()}`;
      continue;
    }

    flushList();
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushList();
  return blocks;
}

/* --------------------------------------------------------------------- copy */

/** "Hartford Comets" → "Hartford Comets'"; "Utah Jazz" → "Utah Jazz's"… for placeholders. */
export function possessive(name: string): string {
  return /s$/i.test(name) ? `${name}’` : `${name}’s`;
}

const PRIORITY_LEVEL_LABEL: Record<string, string> = {
  must_preserve: "Must preserve",
  high: "High",
  medium: "Medium",
  low: "Low",
};

export function priorityLevelLabel(level: string): string {
  return PRIORITY_LEVEL_LABEL[level] ?? level.replace(/_/g, " ");
}

const DOC_TYPE_LABEL: Record<string, string> = {
  scouting_report: "Scouting report",
  stat_profile: "Stat profile",
  analytics_note: "Analytics note",
  team_strategy_memo: "Team strategy memo",
  coaching_note: "Coaching note",
  method_guide: "Method guide",
  agent_pitch: "Agent pitch",
  fan_submission: "Fan submission",
};

export function docTypeLabel(docType: string): string {
  return DOC_TYPE_LABEL[docType] ?? docType.replace(/_/g, " ");
}

const EVIDENCE_CLASS_LABEL: Record<string, string> = {
  observed_statistic: "Observed statistics",
  analyst_opinion: "Analyst opinion",
  team_context: "Team context",
  method_guidance: "Method guidance",
  untrusted_claim: "Untrusted claim",
};

/** What kind of evidence a whole source is; from its document type, never from the model. */
export function evidenceClassLabel(evidenceClass: string | undefined): string | null {
  if (!evidenceClass) return null;
  return EVIDENCE_CLASS_LABEL[evidenceClass] ?? evidenceClass.replace(/_/g, " ");
}

/** The kinds of statement, in reading order, with the words the GM sees. */
export const CLAIM_KINDS: { kind: string; label: string; hint: string }[] = [
  { kind: "observed_fact", label: "Observed facts", hint: "Recorded statistics, checked against the fact sheet" },
  { kind: "analyst_opinion", label: "Analyst views", hint: "A named scout's, coach's or front office's dated judgment" },
  { kind: "inference", label: "Inference", hint: "The assistant's own reasoning for your priorities — nobody documented it" },
  { kind: "unavailable", label: "Not available", hint: "What the sources do not contain" },
  { kind: "untrusted_claim", label: "Untrusted claims", hint: "Something only an agent pitch or public submission says" },
  { kind: "question", label: "Questions for you", hint: "Where the answer depends on your call" },
];

export function claimKindLabel(kind: string): string {
  return CLAIM_KINDS.find((k) => k.kind === kind)?.label ?? kind.replace(/_/g, " ");
}

const UNSUPPORTED_LABEL: Record<string, string> = {
  cba_legality: "CBA / trade legality",
  contracts_and_cap: "Contracts and cap",
  fit_score: "Fit score",
  real_nba_entity: "Real NBA player or team",
  unknown_entity: "Unknown player or team (not in the sandbox)",
  unknown_team: "Team not in the sandbox",
  medical: "Medical",
  playoff_statistics: "Playoff statistics",
};

export function unsupportedLabel(kind: string): string {
  return UNSUPPORTED_LABEL[kind] ?? kind.replace(/_/g, " ");
}

/** "1234.5" ms → "1.23 s"; under a second stays in ms. For the debug timings. */
export function formatMs(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

/** A score to three decimals, or an em dash for "not reported" — never 0. */
export function formatScore(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

/** A trace link is rendered only for a real https URL — never a guessed or generic one. */
export function safeTraceUrl(url: string | null | undefined): string | null {
  if (!url || !url.trim()) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}
