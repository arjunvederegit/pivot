"""The evaluation CLI, run the way its docstring says: `python -m scripts.eval_rag` from backend/.

V1's evaluation script failed with `ModuleNotFoundError: No module named 'app'` because it
was run as a file from a directory that did not put `backend/` on `sys.path`. These tests
run the CLI as a subprocess with `cwd=backend/` — the documented invocation — plus the
file-path form from an unrelated directory, so that regression cannot come back. Commands
that could reach a network (`hosted`) are tested in-process with the key unset and the
LangSmith client stubbed to fail if constructed. Nothing contacts Ollama or LangSmith.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from app.assistant_rag.evaluation import review, runner
from app.assistant_rag.evaluation.dataset import load_benchmark
from app.config import get_settings
from scripts import eval_rag
from tests.unit.test_assistant_eval_runner import StubAnswerer

BACKEND = Path(__file__).resolve().parents[2]
HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None


def run_cli(*args: str, cwd: Path = BACKEND, module: bool = True) -> subprocess.CompletedProcess[str]:
    # No LangSmith credentials reach the child, whatever the developer's shell exports.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LANGSMITH_", "LANGCHAIN_"))}
    command = [sys.executable, "-m", "scripts.eval_rag", *args] if module else [sys.executable, str(BACKEND / "scripts" / "eval_rag.py"), *args]
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180, check=False)


def test_help_runs_as_a_module_from_backend() -> None:
    done = run_cli("--help")
    assert done.returncode == 0, done.stderr
    for command in ("selftest", "retrieval", "local", "hosted", "review"):
        assert command in done.stdout


def test_help_runs_as_a_file_from_anywhere(tmp_path: Path) -> None:
    done = run_cli("local", "--help", cwd=tmp_path, module=False)
    assert done.returncode == 0, done.stderr
    assert "--fake" in done.stdout and "--cases" in done.stdout


def test_selftest_passes_and_prints_the_v1_regression_rows() -> None:
    done = run_cli("selftest")
    assert done.returncode == 0, done.stdout + done.stderr
    v1_block = done.stdout.split("V1 regression", 1)[1].split("\n\n", 1)[0]
    assert "NOT PROVIDED elsewhere" in v1_block
    for scorer in ("abstention_correctness", "claim_polarity", "citation_support"):
        line = next(line for line in v1_block.splitlines() if line.strip().startswith(scorer))
        assert "FAIL (as expected)" in line
    assert "OK:" in done.stdout and "Positive control" in done.stdout


def test_retrieval_without_embeddings_writes_results(tmp_path: Path) -> None:
    done = run_cli("--output-dir", str(tmp_path), "retrieval", "--no-embeddings", "--variants", "lexical", "--k", "3,6", "--quiet")
    assert done.returncode == 0, done.stderr
    assert "Results written to:" in done.stdout
    (directory,) = tmp_path.glob("*-retrieval")
    report = json.loads((directory / "retrieval.json").read_text(encoding="utf-8"))
    assert set(report["aggregates"]["lexical"]) == {"3", "6"}


def test_bad_arguments_are_usage_errors() -> None:
    done = run_cli("local", "--variants", "fancy:hybrid", "--fake")
    assert done.returncode == 2 and "unknown prompt variant" in done.stderr
    done = run_cli("local", "--cases", "no_such_case", "--fake")
    assert done.returncode == 2 and "unknown case id" in done.stderr
    done = run_cli("retrieval", "--k", "three")
    assert done.returncode == 2


@pytest.mark.skipif(not HAS_LANGCHAIN, reason="needs the optional .[assistant] extra")
def test_local_fake_run_writes_results_and_exits_zero(tmp_path: Path) -> None:
    done = run_cli("--output-dir", str(tmp_path), "local", "--fake", "--variants", "grounded:hybrid",
                   "--cases", "fact_vance_three_point,ambiguity_cruz_defense,legality_pure_holloway", "--quiet")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "NOT a quality signal" in done.stderr
    assert "results written to:" in done.stdout
    (directory,) = tmp_path.glob("*-grounded-hybrid")
    assert (directory / "summary.json").exists() and (directory / "report.md").exists()


def test_hosted_without_a_key_is_unverified(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    stub = types.ModuleType("langsmith")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no LangSmith client without a key")

    stub.Client = forbidden  # type: ignore[attr-defined]
    stub.evaluate = forbidden  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langsmith", stub)
    monkeypatch.setattr(get_settings(), "langsmith_api_key", "")
    assert eval_rag.main(["hosted", "--quiet"]) == 2
    out = capsys.readouterr()
    assert "unverified: LANGSMITH_API_KEY not set" in out.out and "PRIVACY" in out.err


def test_review_writes_tallies_and_protects_a_filled_worksheet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert eval_rag.main(["--output-dir", str(tmp_path), "review"]) == 2
    result = runner.run_local(load_benchmark(), variants="grounded:hybrid", case_ids=["teamfit_comets_perimeter"],
                              output_dir=tmp_path, answerer=StubAnswerer())
    worksheet = result.reports[0].directory / review.WORKSHEET_NAME
    worksheet.write_text(worksheet.read_text(encoding="utf-8").replace("- trade_offs: [ ] yes", "- trade_offs: [x] yes"),
                         encoding="utf-8")
    capsys.readouterr()
    assert eval_rag.main(["--output-dir", str(tmp_path), "review"]) == 0
    out = capsys.readouterr().out
    assert "not overwritten" in out and "trade_offs" in out
    assert "[x] yes" in worksheet.read_text(encoding="utf-8")
    assert eval_rag.main(["review", "--tally", str(worksheet)]) == 0
    tally_line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("trade_offs"))
    assert tally_line.split()[1:3] == ["1", "0"]
    assert eval_rag.main(["--output-dir", str(tmp_path), "review", "--force"]) == 0
    assert "[x] yes" not in worksheet.read_text(encoding="utf-8")
