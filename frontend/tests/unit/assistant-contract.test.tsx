/**
 * The frontend half of the Ask Pivot contract test: real backend payloads through the
 * schemas the page validates every response with.
 *
 * `tests/fixtures/assistant-contract.json` is written by the backend
 * (backend/tests/unit/test_assistant_contract_fixture.py) from its real pipeline and
 * service with fakes — one answer per answer kind plus a guarded answer, a roster-fit grid,
 * team-default priorities, an unknown entity and a traced answer; an enabled and a disabled
 * status; the context catalogue. The handwritten fixtures in `assistant-fixtures.ts` can
 * drift from the backend; this file cannot, because the backend test fails until it is
 * regenerated.
 *
 * For each payload this checks that it parses, that parsing changed nothing (no `.default`
 * filled a field the backend left out, no `.catch` swallowed a block the schema could not
 * read), and that every key the backend sent is declared in the schema — `.passthrough()`
 * would otherwise let a renamed field through unnoticed. Then each answer is rendered.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ZodType } from "zod";
import { AssistantAnswer } from "@/components/assistant/answer";
import { claimKindLabel, unsupportedLabel } from "@/lib/assistant-format";
import { askResponseSchema, contextSchema, statusSchema } from "@/lib/assistant-schemas";
import { decodeSession } from "@/lib/assistant-session";
import type { AskRequest, AskResponse } from "@/lib/assistant-types";

const REGENERATE =
  "cd backend && UPDATE_CONTRACT_FIXTURE=1 .venv/bin/pytest -q tests/unit/test_assistant_contract_fixture.py";

interface Contract {
  ask: Record<string, { request: AskRequest; response: Record<string, unknown> }>;
  status: { enabled: Record<string, unknown>; disabled: Record<string, unknown> };
  context: Record<string, unknown>;
}

const contract = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/assistant-contract.json"), "utf8"),
) as Contract;

/* --------------------------------------------------------- schema introspection */

interface Def {
  type: string;
  shape?: Record<string, ZodType>;
  element?: ZodType;
  innerType?: ZodType;
  valueType?: ZodType;
  in?: ZodType;
}
const defOf = (schema: ZodType) => (schema as unknown as { _zod: { def: Def } })._zod.def;

/** Paths of keys in `value` that `schema` does not declare (zod 4 internals, read-only). */
function undeclaredKeys(schema: ZodType, value: unknown, path = "$"): string[] {
  const def = defOf(schema);
  switch (def.type) {
    case "optional":
    case "nullable":
    case "default":
    case "prefault":
    case "catch":
    case "readonly":
    case "nonoptional":
      return value === null || value === undefined ? [] : undeclaredKeys(def.innerType as ZodType, value, path);
    case "pipe":
      return undeclaredKeys(def.in as ZodType, value, path);
    case "array":
      return Array.isArray(value)
        ? value.flatMap((item, i) => undeclaredKeys(def.element as ZodType, item, `${path}[${i}]`))
        : [];
    case "record":
      return value && typeof value === "object"
        ? Object.entries(value).flatMap(([k, v]) => undeclaredKeys(def.valueType as ZodType, v, `${path}.${k}`))
        : [];
    case "object":
      return value && typeof value === "object"
        ? Object.entries(value).flatMap(([k, v]) =>
            def.shape?.[k] ? undeclaredKeys(def.shape[k], v, `${path}.${k}`) : [`${path}.${k}`],
          )
        : [];
    default:
      return [];
  }
}

function expectUnderstood(schema: ZodType, raw: unknown) {
  const parsed = schema.safeParse(raw);
  expect(parsed.success, `does not parse — regenerate? ${REGENERATE}`).toBe(true);
  // Unchanged by parsing: nothing defaulted, nothing caught and replaced.
  expect(parsed.data).toEqual(raw);
  expect(undeclaredKeys(schema, raw), "fields the schema does not declare").toEqual([]);
}

/* ------------------------------------------------------------------------ tests */

describe("the backend's contract fixture", () => {
  it("covers every answer kind, and both status states", () => {
    const kinds = new Set(Object.values(contract.ask).map((c) => c.response.answer_kind));
    expect([...kinds].sort()).toEqual(
      ["abstention", "clarification", "grounded_answer", "partial_abstention", "unsupported_request"],
    );
    expect(contract.status.enabled.enabled).toBe(true);
    expect(contract.status.disabled.enabled).toBe(false);
  });

  it.each(Object.keys(contract.ask))("ask/%s is fully understood by askResponseSchema", (name) => {
    expectUnderstood(askResponseSchema, contract.ask[name].response);
  });

  it.each(["enabled", "disabled"] as const)("status/%s is fully understood by statusSchema", (name) => {
    expectUnderstood(statusSchema, contract.status[name]);
  });

  it("the context catalogue is fully understood by contextSchema", () => {
    expectUnderstood(contextSchema, contract.context);
  });

  it("detects a field the schema does not declare, and a block it had to catch", () => {
    // The checks above are only worth something if they can fail.
    const grounded = contract.ask.grounded_answer.response;
    expect(undeclaredKeys(askResponseSchema, { ...grounded, citations: [] })).toEqual(["$.citations"]);
    const [first, ...rest] = grounded.sources as Record<string, unknown>[];
    expect(
      undeclaredKeys(askResponseSchema, { ...grounded, sources: [{ ...first, snippet: "x" }, ...rest] }),
    ).toEqual(["$.sources[0].snippet"]);
    const malformed = { ...grounded, diagnostics: { k: "six" } };
    expect(askResponseSchema.parse(malformed)).not.toEqual(malformed);
  });

  it("every vocabulary the answers use has a label", () => {
    for (const { response } of Object.values(contract.ask)) {
      const r = response as unknown as AskResponse;
      for (const c of r.claims) expect(claimKindLabel(c.kind), c.kind).not.toBe(c.kind.replace(/_/g, " "));
      for (const u of r.unsupported_requests) expect(unsupportedLabel(u.kind), u.kind).not.toBe(u.kind.replace(/_/g, " "));
    }
  });

  it("every stored answer survives the sessionStorage round trip", () => {
    const messages = Object.entries(contract.ask).flatMap(([name, { request, response }]) => [
      { id: `${name}-q`, role: "user", content: request.question, created_at: "2026-10-17T00:00:00Z", request },
      { id: `${name}-a`, role: "assistant", content: response.answer, created_at: "2026-10-17T00:00:01Z", request, response },
    ]);
    const session = decodeSession(
      JSON.stringify({ version: 1, team_id: null, mode: "research", priorities: {}, constraints: [], messages }),
    );
    expect(session.messages).toHaveLength(messages.length);
    expect(session.messages.filter((m) => m.response).map((m) => m.response)).toEqual(
      Object.values(contract.ask).map((c) => c.response),
    );
  });
});

describe("rendering the backend's real answers", () => {
  function renderCase(name: string, debug = true) {
    const { request, response: raw } = contract.ask[name];
    const response = askResponseSchema.parse(raw) as AskResponse;
    return render(
      <AssistantAnswer
        response={response}
        turnNumber={1}
        sources={response.sources}
        onOpenSource={() => {}}
        onShowSources={() => {}}
        onFollowUp={() => {}}
        followUpsDisabled={false}
        debug={debug}
        selectedSourceId={null}
        dimensionLabel={(key) => key.replace(/_/g, " ")}
        shownInInspector={false}
        request={request}
      />,
    );
  }

  it.each(Object.keys(contract.ask))("%s renders without a React warning", (name) => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container, unmount } = renderCase(name);
    expect(container.textContent).toContain("Fictional data");
    expect(error).not.toHaveBeenCalled();
    error.mockRestore();
    unmount();
  });

  it("debug states the backend's total row, not the sum of every row", () => {
    // Every timing is pinned to 1 ms in the fixture: the sum of the rows would be ~14 ms.
    const { container, unmount } = renderCase("grounded_answer");
    expect(container.textContent).toContain("Stage timings · 1 ms total");
    unmount();
  });

  it("a guarded answer's breakdown shows the removed sentence as removed, not as 'Not available'", () => {
    const { container, unmount } = renderCase("guarded_answer", false);
    const breakdown = within(container).getByRole("region", { name: "How this answer breaks down" });
    expect(breakdown.textContent).not.toContain("⟨");
    expect(within(breakdown).getByRole("list", { name: "Kinds of statement in this answer" }).textContent).not.toContain(
      "Not available",
    );
    unmount();
  });

  it("team-default priorities are labelled as the team's, an unknown entity by name", () => {
    const defaults = renderCase("roster_fit_team_defaults", false);
    expect(defaults.container.textContent).toContain("Priorities used — the Hartford Comets’ documented defaults");
    defaults.unmount();
    const unknown = renderCase("unknown_entity", false);
    expect(unknown.container.textContent).toContain("Unknown player or team (not in the sandbox).");
    unknown.unmount();
  });
});
