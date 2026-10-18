"use client";

/**
 * The question box. Enter sends, Shift+Enter adds a line, and Enter while an input method
 * is composing (Japanese, Chinese, Korean…) is left to the IME — sending half a word
 * because the user pressed Enter to confirm a character is the classic chat-box bug.
 */

import { useId, useLayoutEffect } from "react";
import { MAX_QUESTION_CHARS } from "@/lib/assistant-types";
import { Button } from "@/components/ui";

/** About eight lines of 14px text, plus padding. */
const MAX_HEIGHT = 200;
const COUNTER_FROM = MAX_QUESTION_CHARS - 400;

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  pending,
  disabled,
  disabledReason,
  placeholder,
  textareaRef,
  controls,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  pending: boolean;
  disabled: boolean;
  disabledReason?: string | null;
  placeholder: string;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  /** The compact context strip, shown above the box (team, mode, strategy, constraints, suggestions). */
  controls?: React.ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const canSend = !disabled && !pending && value.trim().length > 0;

  // Grow with the text up to MAX_HEIGHT, then scroll inside. A DOM write, not state.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
    el.style.overflowY = el.scrollHeight > MAX_HEIGHT ? "auto" : "hidden";
  }, [value, textareaRef]);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (canSend) onSend();
      }}
      className="panel p-3 sm:p-3.5"
    >
      {controls}
      {/* A fixed name on the box itself: the placeholder changes with the team and the
          priorities, so it must never be what the box is called. */}
      <textarea
        id={id}
        ref={textareaRef}
        aria-label="Ask Pivot a question about the fictional league"
        rows={2}
        value={value}
        maxLength={MAX_QUESTION_CHARS}
        disabled={disabled}
        aria-describedby={hintId}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.shiftKey) return;
          // keyCode 229 is what some browsers report for keys an IME is still handling.
          if (event.nativeEvent.isComposing || event.keyCode === 229) return;
          event.preventDefault();
          if (canSend) onSend();
        }}
        className="scroll-thin block w-full resize-none rounded-lg border border-line bg-panel2 px-3.5 py-2.5 text-[14px] leading-[1.55] text-foreground transition-colors placeholder:text-faint hover:border-[#4a4a50] focus:border-signal/60 disabled:cursor-not-allowed disabled:opacity-50"
      />
      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
        {/* Hidden on phones unless it explains why the box is locked; it still describes
            the textarea to assistive tech either way (aria-describedby reads hidden text). */}
        <p
          id={hintId}
          className={`min-w-0 flex-1 text-[11.5px] leading-snug text-faint ${disabled ? "" : "hidden sm:block"}`}
        >
          {disabled && disabledReason
            ? disabledReason
            : "Enter to send · Shift+Enter for a new line · answers take 10–60 seconds on a local model"}
        </p>
        {value.length >= COUNTER_FROM && (
          <span
            className={`text-[11.5px] tabular-nums ${
              value.length >= MAX_QUESTION_CHARS ? "text-illegal" : "text-conditional"
            }`}
          >
            {value.length.toLocaleString("en-US")} / {MAX_QUESTION_CHARS.toLocaleString("en-US")}
          </span>
        )}
        {pending ? (
          <Button type="button" variant="secondary" size="sm" className="ml-auto" onClick={onStop}>
            <span aria-hidden className="text-[10px]">
              ■
            </span>
            Stop
          </Button>
        ) : (
          <Button type="submit" variant="primary" size="sm" className="ml-auto" disabled={!canSend}>
            Send
          </Button>
        )}
      </div>
    </form>
  );
}
