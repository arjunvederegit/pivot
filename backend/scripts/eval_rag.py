"""Ask Pivot evaluation CLI — scorer self-test, retrieval sweep, local and hosted runs, review.

Run it from `backend/` as a module (the documented way):

    cd backend
    .venv/bin/python -m scripts.eval_rag selftest
    .venv/bin/python -m scripts.eval_rag retrieval [--variants dense,lexical,hybrid] [--k 3,5,6,8] [--fake-embeddings]
    .venv/bin/python -m scripts.eval_rag local [--variants grounded:hybrid,basic:hybrid] [--cases id1,id2] [--fake] [--k N]
    .venv/bin/python -m scripts.eval_rag hosted [--variants grounded:hybrid,basic:hybrid]
    .venv/bin/python -m scripts.eval_rag review [--run DIR] [--tally FILE]
    .venv/bin/python -m scripts.eval_rag conversations [--conversations id1,id2] [--variants grounded:hybrid] [--fake]

What each needs:

    selftest   nothing (no Ollama, no LangChain): every scorer vs hand-written answers
    retrieval  Ollama embeddings (nomic-embed-text); --fake-embeddings / --no-embeddings: none
    local      Ollama chat + embeddings, sequential (~20-40 min for the full benchmark);
               --fake: nothing but the .[assistant] extra — a harness check, not a quality signal
    hosted     LANGSMITH_API_KEY + Ollama; uploads examples, prompts and answers to LangSmith
    review     a previous `local` run; --tally counts a filled-in worksheet
    conversations  Ollama: scripted multi-turn GM conversations, each turn's history being
               the assistant's own earlier answers; writes a readable transcript.md

Output goes to `backend/.assistant_eval/` (gitignored; `--output-dir` to change), and every
command prints where it wrote. Exit codes: 0 ok, 1 blocking failures (or a self-test
mismatch), 2 environment error (Ollama down, model missing, no API key).

Why `-m`. V1 was run as `python scripts/eval_rag.py`, which puts `scripts/` — not `backend/` —
on `sys.path`, so `import app` failed unless the package happened to be pip-installed in
editable mode. `python -m scripts.eval_rag` from `backend/` puts `backend/` on `sys.path`
(and `scripts/__init__.py` makes `scripts` a package), so `app` always resolves to the source
tree. Running the file directly (`.venv/bin/python scripts/eval_rag.py --help`) also works:
the bootstrap below adds `backend/` to `sys.path` when there is no package context.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a file: make `import app` resolve to this backend/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = BACKEND_DIR / ".assistant_eval"


def _csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _ks(text: str) -> list[int]:
    try:
        return [int(part) for part in _csv(text)]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--k expects comma-separated integers, got {text!r}") from exc


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------- commands


def cmd_selftest(args: argparse.Namespace) -> int:
    from app.assistant_rag.evaluation.runner import format_selftest, run_selftest

    result = run_selftest()
    print(format_selftest(result))
    return result.exit_code


def cmd_retrieval(args: argparse.Namespace) -> int:
    from app.assistant_rag.evaluation.dataset import load_benchmark
    from app.assistant_rag.evaluation.runner import run_retrieval_sweep

    embeddings = "fake" if args.fake_embeddings else "none" if args.no_embeddings else "live"
    result = run_retrieval_sweep(
        load_benchmark(),
        variants=_csv(args.variants),
        ks=args.k,
        embeddings=embeddings,
        output_dir=args.output_dir,
        holdout_path=None if args.no_holdout else _default_holdout(),
        progress=None if args.quiet else _progress,
    )
    report = result.report
    if result.environment_error:
        print(f"ENVIRONMENT ERROR: {result.environment_error}")
    else:
        model = result.embedding_model or "no embedding model"
        print(f"Retrieval sweep (embeddings: {embeddings}, {model}) — recall@k / hit@k on the benchmark:")
        for variant, by_k in report["aggregates"].items():
            cells = "  ".join(f"k={k}: {_num(v['recall'])}/{_num(v['hit_rate'])}" for k, v in by_k.items())
            print(f"  {variant:<8} {cells}")
        for note in report["skipped"]:
            print(f"  skipped {note}")
        print(f"  wall time {report['wall_seconds']:.1f} s")
    print(f"Results written to: {result.directory}")
    return result.exit_code


def _default_holdout() -> Path:
    from app.assistant_rag.evaluation.runner import HOLDOUT_PATH

    return HOLDOUT_PATH


def cmd_local(args: argparse.Namespace) -> int:
    from app.assistant_rag.evaluation.dataset import load_benchmark
    from app.assistant_rag.evaluation.runner import FAKE_NOTE, comparison_table, run_local

    mode = "fake" if args.fake else "live"
    if mode == "fake":
        print(FAKE_NOTE, file=sys.stderr)
    result = run_local(
        load_benchmark(),
        variants=args.variants,
        mode=mode,
        k=args.k,
        case_ids=_csv(args.cases) if args.cases else None,
        output_dir=args.output_dir,
        progress=None if args.quiet else _progress,
    )
    if result.preflight_error:
        print(f"ENVIRONMENT ERROR (nothing was run): {result.preflight_error}")
        print(f"Preflight record written to: {result.output_dir}")
        return result.exit_code
    print("\n".join(comparison_table([r.summary for r in result.reports])))
    for report in result.reports:
        s = report.summary
        print(f"{report.variant.label}: {s['cases']['scored']}/{s['cases']['total']} scored, "
              f"{len(s['blocking_failures'])} blocking failures, {s['cases']['environment_errors']} environment errors, "
              f"{s['wall_seconds']:.1f} s -> exit {report.exit_code}")
        print(f"  results written to: {report.directory}")
    return result.exit_code


def cmd_hosted(args: argparse.Namespace) -> int:
    from app.assistant_rag.evaluation.langsmith_runner import PRIVACY_NOTICE, run_experiments

    print(f"PRIVACY: {PRIVACY_NOTICE}", file=sys.stderr)
    result = run_experiments(args.variants, k=args.k, progress=None if args.quiet else _progress)
    print(result.message)
    if result.dataset is not None:
        print(f"dataset: {result.dataset.name} ({'created' if result.dataset.created else 'reused'})")
    for experiment in result.experiments:
        print(f"  {experiment['variant']}: {experiment['experiment_name']}  {experiment['url'] or ''}")
    return result.exit_code


def cmd_review(args: argparse.Namespace) -> int:
    from app.assistant_rag.evaluation import review
    from app.assistant_rag.evaluation.runner import latest_run_dirs

    if args.tally:
        tally = review.parse_worksheet(Path(args.tally).read_text(encoding="utf-8"))
        print(review.format_tally(tally))
        return 0
    run_dirs = [Path(args.run)] if args.run else latest_run_dirs(args.output_dir)
    if not run_dirs:
        print(f"No local run found under {args.output_dir}. Run: .venv/bin/python -m scripts.eval_rag local")
        return 2
    for run_dir in run_dirs:
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        p = summary["provenance"]
        header = {"run": summary["run_id"], "variant": summary["variant"]["label"], "mode": summary["mode"],
                  "benchmark": p["benchmark_hash"][:12], "corpus": p["corpus_version"]}
        path, written = review.write_worksheet(run_dir, header=header, force=args.force)
        if written:
            print(f"Worksheet written to: {path}")
        else:
            print(f"Worksheet exists (not overwritten; --force regenerates it): {path}")
            print(review.format_tally(review.parse_worksheet(path.read_text(encoding="utf-8"))))
    return 0


def cmd_conversations(args: argparse.Namespace) -> int:
    from app.assistant_rag.evaluation.conversations import run_conversations

    mode = "fake" if args.fake else "live"
    result = run_conversations(
        mode=mode,
        variants=args.variants,
        conversation_ids=_csv(args.conversations) if args.conversations else None,
        output_dir=args.output_dir,
    )
    for record in result.results:
        failed = record.failed()
        turns = len(record.turns)
        print(f"{record.conversation.id} [{record.variant.label}]: {turns} turns, {len(failed)} failed checks")
        for case_id, score in failed:
            print(f"  FAIL {case_id}:{score.key} [{score.severity}] {score.detail[:160]}")
    print(f"Transcript written to: {result.directory / 'transcript.md'}")
    return result.exit_code


# --------------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.eval_rag",
        description="Evaluate Ask Pivot (fictional synthetic_demo corpus). Run from backend/.",
        epilog="Exit codes: 0 ok, 1 blocking failures / self-test mismatch, 2 environment error.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="where results go (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("selftest", help="every scorer vs known-verdict answers (no Ollama needed)")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("retrieval", help="recall@k / hit@k per retrieval variant and k (no LLM calls)")
    p.add_argument("--variants", default="dense,lexical,hybrid", help="comma list (default: %(default)s)")
    p.add_argument("--k", type=_ks, default=[3, 5, 6, 8], help="comma list of k (default: 3,5,6,8)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--fake-embeddings", action="store_true", help="hash vectors: offline, dense numbers meaningless")
    group.add_argument("--no-embeddings", action="store_true", help="skip dense; hybrid = keyword + entity")
    p.add_argument("--no-holdout", action="store_true", help="skip the blind retrieval holdout questions")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_retrieval)

    p = sub.add_parser("local", help="run the pipeline over the benchmark locally and score it")
    p.add_argument("--variants", default="grounded:hybrid,basic:hybrid", help="prompt:retrieval comma list (default: %(default)s)")
    p.add_argument("--cases", default=None, help="comma list of case ids (default: all)")
    p.add_argument("--fake", action="store_true", help="deterministic fake model + embeddings: harness check, NOT a quality signal")
    p.add_argument("--k", type=int, default=None, help="passages per answer (default: the retriever's)")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_local)

    p = sub.add_parser("hosted", help="LangSmith dataset + one experiment per variant (needs LANGSMITH_API_KEY)")
    p.add_argument("--variants", default="grounded:hybrid,basic:hybrid", help="prompt:retrieval comma list (default: %(default)s)")
    p.add_argument("--k", type=int, default=None)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_hosted)

    p = sub.add_parser("review", help="human review worksheet for the latest local run, or --tally a filled one")
    p.add_argument("--run", default=None, help="a run directory (default: every variant of the latest run)")
    p.add_argument("--tally", default=None, help="count a filled-in worksheet")
    p.add_argument("--force", action="store_true", help="regenerate even if a worksheet exists (discards edits)")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("conversations", help="scripted multi-turn GM conversations, scored turn by turn")
    p.add_argument("--conversations", default=None, help="comma list of conversation ids (default: all)")
    p.add_argument("--variants", default="grounded:hybrid", help="prompt:retrieval comma list (default: %(default)s)")
    p.add_argument("--fake", action="store_true", help="deterministic fake model: harness check, NOT a quality signal")
    p.set_defaults(func=cmd_conversations)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:  # bad variant / case id / k: a usage error, not a crash
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
