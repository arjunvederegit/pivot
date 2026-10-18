"use client";

/**
 * The per-answer debug view: what the pipeline actually did.
 *
 * Shown only when the reader turns Debug on. It exists to make the RAG mechanics
 * inspectable — which query text was embedded, which candidates each retriever ranked and
 * which survived fusion, whether the evidence cleared the threshold that decides between
 * calling the model and abstaining, how long each stage took — so a wrong answer can be
 * traced to retrieval, the prompt or the model rather than guessed at.
 *
 * Trace links: rendered only for a real `https` URL the server returned for *this* run.
 * Never a generic LangSmith link — a link that does not open this run's trace is worse
 * than none.
 */

import { formatMs, formatScore, safeTraceUrl } from "@/lib/assistant-format";
import type { AskResponse } from "@/lib/assistant-types";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-faint">{label}</dt>
      <dd className="mt-0.5 break-words text-[12.5px] text-foreground [overflow-wrap:anywhere]">
        {children}
      </dd>
    </div>
  );
}

export function DebugPanel({ response }: { response: AskResponse }) {
  const d = response.diagnostics;
  const trace = response.trace;
  const traceUrl = safeTraceUrl(trace?.url);
  // The backend's own `total` row (start to finish). Never a sum of every row: the list also
  // holds the `retrieval.*` sub-stages, already inside `retrieval`, and `total` itself.
  const total =
    d?.timings.find((t) => t.stage === "total")?.ms ??
    d?.timings.filter((t) => t.stage !== "total" && !t.stage.includes(".")).reduce((sum, t) => sum + t.ms, 0) ??
    0;
  const belowThreshold =
    d && d.top_dense_score !== null && d.top_dense_score < d.min_evidence_threshold;

  return (
    <section
      aria-label="Debug diagnostics"
      className="rounded-lg border border-dashed border-line bg-ink/40 p-4"
    >
      <div className="eyebrow mb-3 flex items-center gap-2">
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-faint" />
        Debug · how this answer was produced
      </div>

      {!d ? (
        <p className="text-[12.5px] text-muted">
          The server’s diagnostics block was missing or malformed, so there is nothing to show.
        </p>
      ) : (
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
            <Row label="Corpus version">
              <code>{d.corpus_version}</code>
            </Row>
            <Row label="Prompt variant">{d.prompt_variant}</Row>
            <Row label="Retrieval variant">
              {d.retrieval_variant} · k={d.k}
            </Row>
            <Row label="Chat model">{d.chat_model ?? "—"}</Row>
            <Row label="Embedding model">{d.embedding_model ?? "—"}</Row>
            <Row label="Model called">
              {d.model_called ? "Yes" : "No — deterministic reply"}
            </Row>
            <Row label="Prompt / output tokens">
              <span className="tabular-nums">
                {d.prompt_tokens ?? "—"} / {d.output_tokens ?? "—"}
              </span>
            </Row>
            <Row label="Top dense score vs threshold">
              <span className="tabular-nums">
                {formatScore(d.top_dense_score)} vs {formatScore(d.min_evidence_threshold)}
              </span>
              {belowThreshold && (
                <span className="ml-1.5 text-conditional">
                  <span aria-hidden>! </span>below threshold
                </span>
              )}
            </Row>
            <Row label="Request id">
              <code>{response.request_id ?? "—"}</code>
            </Row>
          </dl>

          {d.raw_answer && (
            <div>
              <div className="mb-1 text-[11px] text-faint">
                The model’s original answer, before the answer guard removed{" "}
                {response.guard?.removals.length ?? 0} sentence(s)
              </div>
              <pre
                className="scroll-thin max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-dashed border-illegal/35 bg-panel2 p-2.5 text-[12px] leading-relaxed text-muted [overflow-wrap:anywhere]"
                role="region"
                aria-label="The model’s original answer, scrollable"
                tabIndex={0}
              >
                {d.raw_answer}
              </pre>
            </div>
          )}

          <div>
            <div className="mb-1 text-[11px] text-faint">Query text sent to the retrievers</div>
            <pre
              className="scroll-thin max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-hairline bg-panel2 p-2.5 text-[12px] leading-relaxed text-muted [overflow-wrap:anywhere]"
              role="region"
              aria-label="Query text sent to the retrievers, scrollable"
              tabIndex={0}
            >
              {d.query_text}
            </pre>
          </div>

          {d.candidates.length > 0 && (
            // Wider than a phone: focusable, so a keyboard alone can scroll it (as the grid is).
            <div
              className="scroll-thin overflow-x-auto rounded-md border border-hairline"
              role="region"
              aria-label="Retrieval candidates, scrollable"
              tabIndex={0}
            >
              <table className="w-full min-w-[560px] border-collapse text-left text-[12px]">
                <caption className="px-2.5 py-2 text-left text-[11px] text-faint">
                  Retrieval candidates, in fused order — selected ones were given to the model
                </caption>
                <thead>
                  <tr className="border-y border-hairline bg-panel2 text-faint">
                    <th scope="col" className="px-2.5 py-1.5 font-medium">#</th>
                    <th scope="col" className="px-2.5 py-1.5 font-medium">Chunk</th>
                    <th scope="col" className="px-2.5 py-1.5 font-medium">Title</th>
                    <th scope="col" className="px-2.5 py-1.5 text-right font-medium">Fused</th>
                    <th scope="col" className="px-2.5 py-1.5 text-right font-medium">Dense</th>
                    <th scope="col" className="px-2.5 py-1.5 text-right font-medium">Lexical</th>
                    <th scope="col" className="px-2.5 py-1.5 font-medium">Selected</th>
                  </tr>
                </thead>
                <tbody>
                  {d.candidates.map((c, i) => (
                    <tr key={c.chunk_id} className="border-b border-hairline last:border-b-0">
                      <td className="px-2.5 py-1.5 tabular-nums text-faint">{i + 1}</td>
                      <td className="px-2.5 py-1.5">
                        <code className="text-[11.5px] text-muted">{c.chunk_id}</code>
                      </td>
                      <td className="px-2.5 py-1.5 text-muted">{c.title}</td>
                      <td className="px-2.5 py-1.5 text-right tabular-nums">{formatScore(c.fused_score, 4)}</td>
                      <td className="px-2.5 py-1.5 text-right tabular-nums">{c.dense_rank ?? "—"}</td>
                      <td className="px-2.5 py-1.5 text-right tabular-nums">{c.lexical_rank ?? "—"}</td>
                      <td className="px-2.5 py-1.5">
                        {c.selected ? (
                          <span className="text-legal">
                            <span aria-hidden>✓ </span>Selected
                          </span>
                        ) : (
                          <span className="text-faint">
                            <span aria-hidden>— </span>Not used
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {d.timings.length > 0 && (
            <div>
              <div className="mb-1 text-[11px] text-faint">
                Stage timings · <span className="tabular-nums">{formatMs(total)}</span> total
              </div>
              <ol className="grid gap-x-4 gap-y-1 text-[12px] sm:grid-cols-2">
                {d.timings.map((t) => (
                  <li key={t.stage} className="flex justify-between gap-3 border-b border-hairline py-1">
                    <code className="text-muted">{t.stage}</code>
                    <span className="tabular-nums text-foreground">{formatMs(t.ms)}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      <div className="mt-4 border-t border-hairline pt-3 text-[12px] text-muted">
        {trace?.enabled ? (
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium text-foreground">LangSmith trace</span>
            <span>
              run <code className="text-[11.5px]">{trace.run_id ?? "—"}</code>
            </span>
            <span>
              project <code className="text-[11.5px]">{trace.project ?? "—"}</code>
            </span>
            {traceUrl && (
              <a
                href={traceUrl}
                target="_blank"
                rel="noreferrer noopener"
                className="text-signal underline decoration-signal/40 underline-offset-2 hover:text-foreground"
              >
                Open this run’s trace
              </a>
            )}
          </p>
        ) : (
          <p>Tracing was off for this answer — nothing about it was sent to LangSmith.</p>
        )}
      </div>
    </section>
  );
}
