"use client";

/**
 * Renders an answer's Markdown tree (`lib/assistant-format.ts`) as React elements.
 *
 * Every string reaches the DOM as a React text child — escaped by React — so nothing in an
 * answer can become markup. Citations become `CitationChip`s resolved against the sources
 * this answer actually retrieved.
 */

import { Fragment } from "react";
import { parseAnswer, type Block, type Inline } from "@/lib/assistant-format";
import type { AssistantSource } from "@/lib/assistant-types";
import { CitationChip } from "@/components/assistant/citation";

type OpenSource = (sourceId: string, opener: HTMLElement) => void;

function Inlines({
  inlines,
  sources,
  onOpen,
  selectedSourceId,
}: {
  inlines: Inline[];
  sources: AssistantSource[];
  onOpen?: OpenSource;
  selectedSourceId?: string | null;
}) {
  return (
    <>
      {inlines.map((node, i) => {
        switch (node.type) {
          case "text":
            return <Fragment key={i}>{node.text}</Fragment>;
          case "break":
            return <br key={i} />;
          case "removed":
            return (
              <span
                key={i}
                role="note"
                className="mx-0.5 inline rounded border border-dashed border-illegal/45 bg-illegal/[0.06] px-1.5 py-[1px] text-[12px] text-muted"
              >
                <span aria-hidden className="mr-1 text-illegal">
                  ✕
                </span>
                <span className="font-medium text-foreground">Removed by Ask Pivot’s checks:</span> {node.reason}.
              </span>
            );
          case "strong":
            return (
              <strong key={i} className="font-semibold text-foreground">
                <Inlines
                  inlines={node.children}
                  sources={sources}
                  onOpen={onOpen}
                  selectedSourceId={selectedSourceId}
                />
              </strong>
            );
          case "citation":
            return (
              <span key={i} className="mx-0.5 inline-flex flex-wrap gap-0.5 align-baseline">
                {node.ids.map((id) => (
                  <CitationChip
                    key={id}
                    id={id}
                    source={sources.find((s) => s.source_id === id)}
                    onOpen={onOpen}
                    selected={selectedSourceId === id}
                  />
                ))}
              </span>
            );
        }
      })}
    </>
  );
}

function BlockView({
  block,
  sources,
  onOpen,
  selectedSourceId,
}: {
  block: Block;
  sources: AssistantSource[];
  onOpen?: OpenSource;
  selectedSourceId?: string | null;
}) {
  const shared = { sources, onOpen, selectedSourceId };
  if (block.type === "heading") {
    return (
      <h4 className="eyebrow pt-2 text-foreground">
        <Inlines inlines={block.inlines} {...shared} />
      </h4>
    );
  }
  if (block.type === "list") {
    const items = block.items.map((item, i) => (
      <li key={i} className="pl-1">
        <Inlines inlines={item} {...shared} />
      </li>
    ));
    return block.ordered ? (
      <ol start={block.start} className="list-decimal space-y-1.5 pl-5 marker:text-faint">
        {items}
      </ol>
    ) : (
      <ul className="list-disc space-y-1.5 pl-5 marker:text-faint">{items}</ul>
    );
  }
  return (
    <p>
      <Inlines inlines={block.inlines} {...shared} />
    </p>
  );
}

export function AnswerBody({
  text,
  sources,
  onOpen,
  selectedSourceId,
}: {
  text: string;
  sources: AssistantSource[];
  onOpen?: OpenSource;
  selectedSourceId?: string | null;
}) {
  const blocks = parseAnswer(text);
  return (
    <div className="max-w-[72ch] space-y-3 text-[14.5px] leading-[1.65] text-foreground/90 [overflow-wrap:anywhere]">
      {blocks.map((block, i) => (
        <BlockView
          key={i}
          block={block}
          sources={sources}
          onOpen={onOpen}
          selectedSourceId={selectedSourceId}
        />
      ))}
    </div>
  );
}
