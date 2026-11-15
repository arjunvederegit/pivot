import { useMutation } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useReducer, useRef } from "react";
import { api, ApiError } from "@/lib/api";
import { evaluateTrade, type TradeEvaluationRequest, type TradeEvaluationResult } from "@/lib/tradeEvaluate";
import type { LegalityResponse, PickMove, PlayerMove } from "@/lib/types";

/** What the live rules check sends to `POST /trades/validate`. */
export interface TradeValidationRequest {
  team_ids: string[];
  player_moves: PlayerMove[];
  pick_moves: PickMove[];
}

/** How long the draft must be still before the rules are re-checked. */
export const VALIDATION_DEBOUNCE_MS = 450;

/**
 * The Trade Evaluator's two asynchronous results — the evaluation and the live rules check — and
 * the pending state of each, kept so that a response can only land on the screen state it was
 * requested for.
 *
 * Three ideas, kept apart:
 *
 * - INPUT CONTEXT. Each channel remembers the request its data and pending work belong to (`sig`,
 *   a JSON copy of the request fields, so a re-render that rebuilds equal arrays is the same
 *   context) and the saved-trade identity (`key`). When either differs from what the page now
 *   passes, the channel moves to a new `epoch` with nothing in it. That happens in the render that
 *   carries the new inputs (React discards that pass and re-renders before anything is committed),
 *   so no committed render ever pairs the new inputs with the old analysis. Because the epoch only
 *   moves forward, returning to earlier inputs (A, B, A) is a new context and revives nothing.
 * - REQUEST OWNERSHIP. Within an epoch, each request gets an id. Only the epoch's newest request
 *   may publish, report an error, or clear `pending`; an older one is ignored whatever it returns.
 *   The evaluation and the rules check are separate channels, so neither invalidates the other.
 * - ADOPTION. `adopt` installs an authoritative result for a stated request in a fresh epoch, which
 *   supersedes anything already in flight even for identical inputs. Passing a different `draftKey`
 *   (a saved trade replacing the draft) does the same for both channels.
 *
 * Nothing is cancelled: obsolete requests run to completion and are then ignored.
 */

interface Channel<T> {
  /** The saved-trade identity this channel belongs to. */
  key: string | null;
  /** The request (as JSON) this channel's data and pending work belong to; `null` = nothing to request. */
  sig: string | null;
  /** Advances whenever the context changes or is cleared; a token from an older epoch is obsolete. */
  epoch: number;
  /** Id of the newest request started in this epoch (0 = none). */
  latest: number;
  pending: boolean;
  data: T | null;
  /** Why the newest request of this epoch failed, when it did; only the rules check records one. */
  error: string | null;
}

interface State {
  evaluation: Channel<TradeEvaluationResult>;
  validation: Channel<LegalityResponse>;
}

type Step<T> =
  | { type: "align"; key: string | null; sig: string | null }
  | { type: "reset" }
  | { type: "adopt"; sig: string; data: T }
  | { type: "start"; epoch: number; id: number; clear: boolean }
  | { type: "succeed"; epoch: number; id: number; data: T }
  | { type: "fail"; epoch: number; id: number; clear: boolean; error?: string };

type Action =
  | ({ channel: "evaluation" } & Step<TradeEvaluationResult>)
  | ({ channel: "validation" } & Step<LegalityResponse>);

const fresh = <T,>(key: string | null, sig: string | null, epoch: number): Channel<T> => ({
  key,
  sig,
  epoch,
  latest: 0,
  pending: false,
  data: null,
  error: null,
});

function step<T>(channel: Channel<T>, action: Step<T>): Channel<T> {
  switch (action.type) {
    case "align":
      return channel.key === action.key && channel.sig === action.sig
        ? channel
        : fresh(action.key, action.sig, channel.epoch + 1);
    case "reset":
      return fresh(channel.key, channel.sig, channel.epoch + 1);
    case "adopt":
      return { ...fresh<T>(channel.key, action.sig, channel.epoch + 1), data: action.data };
    case "start":
      return action.epoch !== channel.epoch
        ? channel
        : { ...channel, latest: action.id, pending: true, data: action.clear ? null : channel.data, error: null };
    case "succeed":
      return action.epoch !== channel.epoch || action.id !== channel.latest
        ? channel
        : { ...channel, pending: false, data: action.data, error: null };
    case "fail":
      return action.epoch !== channel.epoch || action.id !== channel.latest
        ? channel
        : { ...channel, pending: false, data: action.clear ? null : channel.data, error: action.error ?? null };
  }
}

function reduce(state: State, action: Action): State {
  if (action.channel === "evaluation") {
    const next = step(state.evaluation, action);
    return next === state.evaluation ? state : { ...state, evaluation: next };
  }
  const next = step(state.validation, action);
  return next === state.validation ? state : { ...state, validation: next };
}

/* ------------------------------------------------------------- request identity */

// Signatures list the request fields in a fixed order, so equal contents are equal signatures.
// The fields are exactly the ones each endpoint is sent; the rules check takes no scenario or strategy.
const evaluationSignature = (r: TradeEvaluationRequest) =>
  JSON.stringify([r.team_ids, r.player_moves, r.pick_moves, r.scenario_id, r.strategy]);
const validationSignature = (r: TradeValidationRequest) =>
  JSON.stringify([r.team_ids, r.player_moves, r.pick_moves]);

// A request is sent from a copy taken from its signature, so what goes on the wire is the snapshot
// that was current when the request was made, never whatever the page holds when it is sent.
function evaluationRequestFrom(sig: string): TradeEvaluationRequest {
  const [team_ids, player_moves, pick_moves, scenario_id, strategy] = JSON.parse(sig);
  return { team_ids, player_moves, pick_moves, scenario_id, strategy };
}
function validationRequestFrom(sig: string): TradeValidationRequest {
  const [team_ids, player_moves, pick_moves] = JSON.parse(sig);
  return { team_ids, player_moves, pick_moves };
}

/**
 * The sentence shown for a rules check that failed: the server's own message when the client
 * produced one (`ApiError`), otherwise a short stable fallback. Never an object, a stack or markup.
 */
const VALIDATION_FAILURE_FALLBACK = "The request to the rules engine did not complete.";
function validationFailureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const message = typeof error.message === "string" ? error.message.trim() : "";
    if (message && message !== "[object Object]") return message;
  }
  return VALIDATION_FAILURE_FALLBACK;
}

/* --------------------------------------------------------------------- the hook */

interface Options {
  /** Identity of the saved trade the draft was adopted from, if any; a change supersedes all earlier work. */
  draftKey: string | null;
  evaluationRequest: TradeEvaluationRequest;
  /** `null` while the draft cannot be checked (fewer than two teams, or no player moving). */
  validationRequest: TradeValidationRequest | null;
  /** Called for the current evaluation's failure, never for an obsolete one. */
  onEvaluationError: (error: unknown) => void;
}

/**
 * What a success callback is told about the request that produced its result: the accepted `result`
 * (the very object the screen state will hold) and a predicate, bound to that request, that is true
 * only while it still owns the evaluation for the committed screen. It is meant to be asked later,
 * when delayed work runs, and it decides by the same rule as the callback itself.
 */
export interface AcceptedEvaluation {
  result: TradeEvaluationResult;
  isCurrent: () => boolean;
}

interface EvaluationJob {
  epoch: number;
  id: number;
  request: TradeEvaluationRequest;
  onSuccess?: (result: TradeEvaluationResult, accepted: AcceptedEvaluation) => void;
}

/** What the committed screen state says is current, for callbacks that must decide synchronously. */
interface Committed {
  mounted: boolean;
  state: State;
}

/**
 * Whether an evaluation's success callback or error notification still belongs to the screen: the
 * context it was started in is the committed one, and no newer evaluation has started since.
 * `newest` is recorded synchronously when a request starts, so this does not wait for that start to
 * be committed and mirrored; the committed epoch is what makes an edit or clear obsolete it.
 */
const ownsEvaluation = (committed: Committed, newest: number, job: EvaluationJob) =>
  committed.mounted && committed.state.evaluation.epoch === job.epoch && newest === job.id;

export function useTradeAnalysis({ draftKey, evaluationRequest, validationRequest, onEvaluationError }: Options) {
  const evaluationSig = evaluationSignature(evaluationRequest);
  const validationSig = validationRequest ? validationSignature(validationRequest) : null;

  const [state, dispatch] = useReducer(reduce, undefined, (): State => ({
    evaluation: fresh(draftKey, evaluationSig, 0),
    validation: fresh(draftKey, validationSig, 0),
  }));

  // Move a channel to a new epoch in the render that carries new inputs. React re-renders at once
  // with the updated state and never commits this pass, so nothing stale is ever exposed.
  let view = state;
  for (const [channel, sig] of [
    ["evaluation", evaluationSig],
    ["validation", validationSig],
  ] as const) {
    if (view[channel].key !== draftKey || view[channel].sig !== sig) {
      const action: Action = { channel, type: "align", key: draftKey, sig };
      dispatch(action);
      view = reduce(view, action);
    }
  }

  const nextId = useRef(0);
  // Id of the newest evaluation request that was eligible to start. Evaluation only: validation
  // requests draw from `nextId` too, and must not become the evaluation's owner.
  const newestEvaluation = useRef(0);
  const committed = useRef<Committed>({ mounted: false, state });
  useLayoutEffect(() => {
    committed.current.state = state;
  });
  useLayoutEffect(() => {
    const live = committed.current;
    live.mounted = true;
    return () => {
      live.mounted = false;
    };
  }, []);
  const reportError = useRef(onEvaluationError);
  useLayoutEffect(() => {
    reportError.current = onEvaluationError;
  });

  const mutation = useMutation({
    mutationFn: (job: EvaluationJob) => evaluateTrade(job.request),
    // A completion always reaches the reducer, which alone decides (in order, after the matching
    // `start`) what is displayed. Ownership of the side effects is decided separately, above.
    onSuccess: (result, job) => {
      if (!committed.current.mounted) return;
      dispatch({ channel: "evaluation", type: "succeed", epoch: job.epoch, id: job.id, data: result });
      if (ownsEvaluation(committed.current, newestEvaluation.current, job)) {
        job.onSuccess?.(result, {
          result,
          isCurrent: () => ownsEvaluation(committed.current, newestEvaluation.current, job),
        });
      }
    },
    onError: (error, job) => {
      if (!committed.current.mounted) return;
      dispatch({ channel: "evaluation", type: "fail", epoch: job.epoch, id: job.id, clear: false });
      if (ownsEvaluation(committed.current, newestEvaluation.current, job)) reportError.current(error);
    },
  });

  // ---- live rules check (backend-authoritative; debounced) ----
  const validationEpoch = view.validation.epoch;
  useEffect(() => {
    if (validationSig === null) return;
    const request = validationRequestFrom(validationSig);
    const handle = setTimeout(() => {
      const live = committed.current;
      // The inputs moved on after this timer was set but before its cleanup ran.
      if (!live.mounted || live.state.validation.epoch !== validationEpoch) return;
      const id = ++nextId.current;
      dispatch({ channel: "validation", type: "start", epoch: validationEpoch, id, clear: false });
      api.post<LegalityResponse>("/trades/validate", request).then(
        (result) => {
          if (committed.current.mounted)
            dispatch({ channel: "validation", type: "succeed", epoch: validationEpoch, id, data: result });
        },
        (error) => {
          if (committed.current.mounted)
            dispatch({
              channel: "validation",
              type: "fail",
              epoch: validationEpoch,
              id,
              clear: true,
              error: validationFailureMessage(error),
            });
        },
      );
    }, VALIDATION_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [validationSig, validationEpoch]);

  return {
    /** The evaluation of exactly the inputs now on the screen, or `null`. */
    evaluation: view.evaluation.data,
    /** The newest evaluation request for the inputs now on the screen is still running. */
    evaluating: view.evaluation.pending,
    /**
     * Evaluate the inputs now on the screen. Clears the previous output at once, including for
     * identical inputs. `onSuccess` runs only if this request is still the one that owns the screen.
     */
    evaluate: (options?: { onSuccess?: (result: TradeEvaluationResult, accepted: AcceptedEvaluation) => void }) => {
      const epoch = view.evaluation.epoch;
      // A caller retained from a context that is no longer the committed screen's (or from before
      // unmount) is refused before any work starts: it must not send a request, nor take over
      // notification ownership from the request that is current.
      const live = committed.current;
      if (!live.mounted || live.state.evaluation.epoch !== epoch) return;
      const id = ++nextId.current;
      newestEvaluation.current = id;
      dispatch({ channel: "evaluation", type: "start", epoch, id, clear: true });
      mutation.mutate({ epoch, id, request: evaluationRequestFrom(evaluationSig), onSuccess: options?.onSuccess });
    },
    /** Drop the displayed evaluation and supersede any in flight (an edit that does not change the request). */
    clearEvaluation: () => dispatch({ channel: "evaluation", type: "reset" }),
    /**
     * Install an authoritative evaluation for `request` (for example one stored with a saved trade).
     * It supersedes every earlier evaluation request, and is shown only while `request` is what the
     * page is evaluating; if the inputs have moved on it is discarded rather than shown.
     */
    adopt: (request: TradeEvaluationRequest, result: TradeEvaluationResult) =>
      dispatch({ channel: "evaluation", type: "adopt", sig: evaluationSignature(request), data: result }),
    /** The rules check of exactly the inputs now on the screen, or `null`. */
    validation: view.validation.data,
    validating: view.validation.pending,
    /** Why the newest rules check of exactly the inputs now on the screen failed, or `null`. Never set alongside `validation`. */
    validationError: view.validation.error,
  };
}
