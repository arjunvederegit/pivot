/**
 * The answer renderer's parser, and the safety property the renderer exists for.
 *
 * Citation parsing must match the backend's `claims.py` exactly: the chip a reader clicks
 * and the id the citation checker validated have to be the same id, or "cited" and
 * "checked" silently mean different things.
 */

import { render } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import {
  extractCitationIds,
  isRemovedMarker,
  normaliseSourceId,
  parseAnswer,
  parseInline,
  possessive,
  safeTraceUrl,
  splitRemovedMarkers,
  unsupportedLabel,
  type Block,
  type Inline,
} from "@/lib/assistant-format";
import { AnswerBody } from "@/components/assistant/answer-body";
import { sourceFixture } from "./assistant-fixtures";

function citations(inlines: Inline[]): string[][] {
  return inlines.flatMap((n) =>
    n.type === "citation" ? [n.ids] : n.type === "strong" ? citations(n.children) : [],
  );
}

describe("citation ids", () => {
  it("normalises like the backend: [S3] and [s003] both mean S03", () => {
    expect(normaliseSourceId("S3")).toBe("S03");
    expect(normaliseSourceId("s03")).toBe("S03");
    expect(normaliseSourceId("S003")).toBe("S03");
    expect(normaliseSourceId("S123")).toBe("S123");
  });

  it("reads single and grouped citations, in first-appearance order, de-duplicated", () => {
    expect(extractCitationIds("Reed contains well [S03]. Vance less so [S05, S06]. Again [S3].")).toEqual([
      "S03",
      "S05",
      "S06",
    ]);
  });

  it("accepts every separator the backend accepts", () => {
    expect(extractCitationIds("[S01; S02 / S03 & S04]")).toEqual(["S01", "S02", "S03", "S04"]);
  });

  it("does not treat other bracketed text as a citation", () => {
    // "(S03)" used to be excluded here; it is now a citation (see "parenthesised citations"):
    // the live local model writes it as often as "[S03]".
    expect(extractCitationIds("[note] [S] [Sx1] S04 [S1234] (S1234)")).toEqual([]);
  });
});

describe("inline parsing", () => {
  it("turns [S03] and [S03, S05] into citation nodes", () => {
    const nodes = parseInline("Holds up [S03] and switches [S03, S05].");
    expect(citations(nodes)).toEqual([["S03"], ["S03", "S05"]]);
    expect(nodes[0]).toEqual({ type: "text", text: "Holds up " });
  });

  it("keeps the raw citation text for each chip", () => {
    const nodes = parseInline("x [ s3 ,S5 ]");
    const cite = nodes.find((n) => n.type === "citation");
    expect(cite).toEqual({ type: "citation", ids: ["S03", "S05"], raw: "[ s3 ,S5 ]" });
  });

  it("parses **bold**, including a citation inside it", () => {
    const nodes = parseInline("**Strong on-ball defender [S03]** overall");
    expect(nodes[0].type).toBe("strong");
    expect(citations(nodes)).toEqual([["S03"]]);
  });

  it("leaves an unclosed ** as literal text", () => {
    expect(parseInline("a ** b")).toEqual([{ type: "text", text: "a ** b" }]);
  });
});

describe("block parsing", () => {
  const text = [
    "### Point-of-attack defense",
    "Reed is the best screen navigator [S03].",
    "He fouls more in the playoffs [S32].",
    "",
    "**Limitations:**",
    "- No playoff tracking data",
    "* Reports disagree on Vance [S05, S06]",
    "",
    "1. Ask about Holloway",
    "2. Compare spacing",
  ].join("\n");
  const blocks = parseAnswer(text);

  it("recognises headings, '**Label**' lines, paragraphs and both list kinds", () => {
    expect(blocks.map((b) => b.type)).toEqual(["heading", "paragraph", "heading", "list", "list"]);
    const unordered = blocks[3] as Extract<Block, { type: "list" }>;
    const ordered = blocks[4] as Extract<Block, { type: "list" }>;
    expect(unordered.ordered).toBe(false);
    expect(unordered.items).toHaveLength(2);
    expect(ordered.ordered).toBe(true);
    expect(ordered.start).toBe(1);
  });

  it("strips the label line's trailing colon", () => {
    expect(blocks[2]).toEqual({ type: "heading", inlines: [{ type: "text", text: "Limitations" }] });
  });

  it("keeps a single line break inside a paragraph", () => {
    const p = blocks[1] as Extract<Block, { type: "paragraph" }>;
    expect(p.inlines.some((n) => n.type === "break")).toBe(true);
  });

  it("is total: empty and whitespace-only input yield no blocks", () => {
    expect(parseAnswer("")).toEqual([]);
    expect(parseAnswer("  \n\n ")).toEqual([]);
  });
});

describe("no HTML injection", () => {
  const hostile = 'Ignore the above <img src=x onerror="alert(1)"> and <script>alert(2)</script> [S03]';

  it("the parser keeps markup as plain text", () => {
    const [block] = parseAnswer(hostile);
    expect(block.type).toBe("paragraph");
    const text = (block as Extract<Block, { type: "paragraph" }>).inlines
      .filter((n): n is Extract<Inline, { type: "text" }> => n.type === "text")
      .map((n) => n.text)
      .join("");
    expect(text).toContain('<img src=x onerror="alert(1)">');
  });

  it("the renderer shows it as characters and creates no elements from it", () => {
    const { container, unmount } = render(
      createElement(AnswerBody, { text: hostile, sources: [sourceFixture()], onOpen: () => {} }),
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(container.textContent).toContain("<script>alert(2)</script>");
    unmount();
  });

  it("renders a retrieved citation as a labelled button and an unretrieved one as inert", () => {
    const { container, unmount } = render(
      createElement(AnswerBody, {
        text: "Fine [S03]. Invented [S40].",
        sources: [sourceFixture()],
        onOpen: () => {},
      }),
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("aria-label")).toBe("Source S03: Niko Reed — defensive evaluation");
    expect(container.textContent).toContain("S40");
    expect(container.textContent).toContain("not retrieved");
    unmount();
  });
});

describe("copy helpers", () => {
  it("forms possessives for team names", () => {
    expect(possessive("Hartford Comets")).toBe("Hartford Comets’");
    expect(possessive("Omaha Club")).toBe("Omaha Club’s");
  });

  it("only passes real https trace links", () => {
    expect(safeTraceUrl("https://smith.langchain.com/o/x/r/abc")).toBe("https://smith.langchain.com/o/x/r/abc");
    expect(safeTraceUrl("http://smith.langchain.com/r/abc")).toBeNull();
    expect(safeTraceUrl("javascript:alert(1)")).toBeNull();
    expect(safeTraceUrl("")).toBeNull();
    expect(safeTraceUrl(null)).toBeNull();
  });
});

describe("answer guard markers and cited-id mentions", () => {
  it("parses a guard marker as a 'removed' inline, never as text with citations", () => {
    const inlines = parseInline(
      "Vance fits. ⟨Removed by Ask Pivot's checks: it gave the 3P% of Theo Vance as 10.4%; the fact sheet has Theo Vance 40.4% (documented in S04, S05).⟩",
    );
    const removed = inlines.find((n) => n.type === "removed");
    expect(removed).toEqual({
      type: "removed",
      reason: "it gave the 3P% of Theo Vance as 10.4%; the fact sheet has Theo Vance 40.4% (documented in S04, S05)",
    });
    expect(inlines.some((n) => n.type === "citation")).toBe(false);
  });

  it("treats an id named after a verb of citing as reported speech, not a citation", () => {
    const reported = parseInline("The submission told the assistant to cite source [S99] as fact [S30].");
    const citations = reported.filter((n) => n.type === "citation");
    expect(citations).toEqual([{ type: "citation", ids: ["S30"], raw: "[S30]" }]);
    expect(parseInline("According to source [S04], Vance shoots 40.4%.").some((n) => n.type === "citation")).toBe(true);
  });
});

describe("parenthesised citations", () => {
  it("reads (S03) and (S03, S05) as citations, like the backend checker", () => {
    expect(extractCitationIds("Reed grades A (S03). Vance (S05, S06) and [S04].")).toEqual(["S03", "S05", "S06", "S04"]);
    const inlines = parseInline("Whitfield's report (S03): defense A.");
    expect(inlines.filter((n) => n.type === "citation")).toEqual([{ type: "citation", ids: ["S03"], raw: "(S03)" }]);
  });

  it("never reads ordinary parentheses as citations", () => {
    expect(extractCitationIds("He shot 40.4% (on 6.1 attempts) in 2025-26 (regular season).")).toEqual([]);
  });
});

describe("reported speech: parity with claims._MENTION_BEFORE", () => {
  // Each row is the backend's own answer: `extract_citations(text)` in
  // backend/app/assistant_rag/claims.py, run on the same string.
  const cases: [string, string[]][] = [
    // the first alternative: "cite (it/this/them) (as) (a) (fake/nonexistent) (source) (id)"
    ["Instructed to cite source [S99].", []],
    ["The submission told the assistant to cite source [S99] as fact [S30].", ["S30"]],
    // the second: "cite (a/an/the) <up to two words> source|id|reference"
    ["The pitch tells the assistant to cite the planted source [S99].", []],
    ["It asks you to cite an invented reference [S12] for the claim.", []],
    ["The document asks readers to cite the fabricated id [S77] instead.", []],
    ["The fan letter cites an internal source [S99] that does not exist.", []],
    // Python's \w is Unicode-aware: "Müller" is one word there, so it must be one here.
    ["It asks you to cite the Müller reference [S12].", []],
    // not mentions: three words is past the window, "sources" is not "source", no verb
    ["They cited one more fake reference [S88].", ["S88"]],
    ["Cite the three planted sources [S99] here.", ["S99"]],
    ["According to source [S04], Vance shoots 40.4%.", ["S04"]],
    ["Reed defends well [S03], as the scouting source [S05] says.", ["S03", "S05"]],
  ];

  it.each(cases)("%s", (text, ids) => {
    expect(extractCitationIds(text)).toEqual(ids);
    expect(citations(parseInline(text)).flat()).toEqual(ids);
  });
});

describe("guard marker helpers", () => {
  const marker = "⟨Removed by Ask Pivot's checks: it stated a trade-legality verdict; the sandbox has no contract data or CBA engine.⟩";

  it("knows a marker-only sentence from a sentence with a marker glued on", () => {
    expect(isRemovedMarker(marker)).toBe(true);
    expect(isRemovedMarker(`  ${marker}  `)).toBe(true);
    expect(isRemovedMarker(`Theo Vance shoots 40.4% from three [S04]. ${marker}`)).toBe(false);
    expect(isRemovedMarker("Theo Vance shoots 40.4% from three [S04].")).toBe(false);
  });

  it("splits markers out of text, reason without its full stop", () => {
    expect(splitRemovedMarkers(`Vance shoots 40.4% [S04]. ${marker}`)).toEqual([
      { type: "text", text: "Vance shoots 40.4% [S04]. " },
      {
        type: "removed",
        reason: "it stated a trade-legality verdict; the sandbox has no contract data or CBA engine",
      },
    ]);
    expect(splitRemovedMarkers("plain")).toEqual([{ type: "text", text: "plain" }]);
  });
});

describe("unsupported request labels", () => {
  it("labels unknown_entity like its siblings", () => {
    expect(unsupportedLabel("unknown_entity")).toBe("Unknown player or team (not in the sandbox)");
  });

  it("has a proper label for every UnsupportedKind the backend can send", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const models = readFileSync(resolve(__dirname, "../../../backend/app/assistant_rag/models.py"), "utf8");
    const literal = /^UnsupportedKind = Literal\[([\s\S]*?)\]/m.exec(models);
    expect(literal).not.toBeNull();
    const kinds = [...(literal?.[1] ?? "").matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    expect(kinds).toContain("unknown_entity");
    // The fallback is the raw kind with spaces — what a missing label looks like on screen.
    expect(kinds.filter((k) => unsupportedLabel(k) === k.replace(/_/g, " "))).toEqual([]);
  });
});
