"""The human half of the evaluation: a review worksheet for what no scorer can judge.

The deterministic scorers check what is checkable — a number against the manifest, a
direction against an analyst's grade, a citation against what the cited source carries, a
refusal where one is required. They say nothing about whether a team-fit recommendation is
*good*: whether it weighs what each option costs, argues against its own lean, hands the
judgment call back to the GM, and actually follows the priorities the GM set. Scoring that
with keyword lists would be theatre, and an LLM judge would be one more model to evaluate.
So those cases get a worksheet a person fills in.

`render_worksheet` writes one section per subjective case (team fit, goal revision, the
"what would make us regret this" counter-evidence case, head-to-head comparisons): the
request, the answer, what was retrieved and cited, the case's own rubric from the benchmark,
the deterministic results for context, and five yes / no / n-a lines. `parse_worksheet`
reads a filled worksheet back into counts, so a review is a number you can compare across
runs, not a vibe.

Input rows are the lines of a run's `results.jsonl` (plain dicts), so a worksheet can be
regenerated from any past run. Pure Python.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Categories whose quality is a judgment call: they get a worksheet section.
REVIEW_CATEGORIES: tuple[str, ...] = ("team_fit", "goal_revision", "counterevidence", "comparison")
#: (key, question) for each review line. Keys are stable: `parse_worksheet` reads them.
CRITERIA: tuple[tuple[str, str], ...] = (
    ("evidence_supported", "Is every claim backed by a cited source? (spot-check at least two citations)"),
    ("trade_offs", "Does it state what each option costs, not only what it offers?"),
    ("counterargument", "Does it give the strongest evidence against its own lean?"),
    ("asks_gm", "Does it hand the judgment call back to the GM, with a question where the call is theirs?"),
    ("respects_priorities", "Does it follow the GM's stated priorities and constraints (and say so when they changed)?"),
)
VERDICTS: tuple[str, ...] = ("yes", "no", "n/a")
WORKSHEET_NAME = "review_worksheet.md"
RESULTS_NAME = "results.jsonl"

_CASE_HEADING = re.compile(r"^## case: (?P<id>[a-z0-9_]+)\s*$")
_CRITERION_LINE = re.compile(
    r"^- (?P<key>[a-z_]+): \[(?P<yes>[ xX])\] yes \[(?P<no>[ xX])\] no \[(?P<na>[ xX])\] n/a\b"
)
_NOTES_LINE = re.compile(r"^Reviewer notes:\s*(?P<rest>.*)$")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_EXCERPT_CHARS = 4000


# --------------------------------------------------------------------------- selection


def read_results(path: Path) -> list[dict[str, Any]]:
    """The rows of a `results.jsonl`."""
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def needs_review(row: Mapping[str, Any]) -> bool:
    return str((row.get("case") or {}).get("category")) in REVIEW_CATEGORIES


# ---------------------------------------------------------------------------- rendering


def _ids(values: Iterable[Any]) -> str:
    items = list(dict.fromkeys(str(v) for v in values if v))
    return ", ".join(items) if items else "none"


def _levels(priorities: Mapping[str, Any] | None) -> str:
    if not priorities:
        return "none set"
    return ", ".join(f"{k}={v}" for k, v in priorities.items())


def _quote(text: str) -> str:
    text = text.strip() or "(empty)"
    if len(text) > _EXCERPT_CHARS:
        text = text[: _EXCERPT_CHARS - 1] + "…"
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def _score_line(scores: Sequence[Mapping[str, Any]]) -> str:
    verdicts = [s for s in scores if s.get("passed") is not None]
    failed = [s for s in verdicts if s.get("passed") is False]
    line = f"{len(verdicts) - len(failed)} passed, {len(failed)} failed"
    if failed:
        line += " — " + "; ".join(f"{s.get('key')}: {str(s.get('detail', ''))[:140]}" for s in failed[:4])
    return line


def _case_section(row: Mapping[str, Any]) -> list[str]:
    case = row.get("case") or {}
    response = row.get("response") or {}
    history = case.get("history") or []
    out = [f"## case: {case.get('id', row.get('case_id'))}", ""]
    out.append(
        f"- category: {case.get('category')} · severity: {case.get('severity')} · mode: {case.get('mode')}"
        f" · team: {case.get('team_id') or 'none'}"
    )
    out.append(f"- priorities: {_levels(case.get('priorities'))}")
    if case.get("previous_priorities") is not None:
        out.append(f"- previous priorities: {_levels(case.get('previous_priorities'))}")
    if case.get("constraints"):
        out.append(f"- constraints: {'; '.join(case['constraints'])}")
    if history:
        users = [h["content"] for h in history if h.get("role") == "user"]
        latest = f' (latest GM turn: "{users[-1][:160]}")' if users else ""
        out.append(f"- earlier turns: {len(history)}{latest}")
    out.append(f"- question: {case.get('question')}")
    out.append(f"- rubric (from the benchmark): {case.get('rubric')}")
    if case.get("pair_with"):
        pair = [s for s in row.get("scores") or [] if s.get("key") == "objective_sensitivity"]
        detail = f" · objective_sensitivity: {'PASS' if pair[0].get('passed') else 'FAIL'} ({pair[0].get('detail')})" if pair else ""
        out.append(f"- paired with: {case['pair_with']}{detail}")
    if response:
        sources = [s.get("source_id") for s in response.get("sources") or []]
        out.append(f"- answer kind: {response.get('answer_kind')} · status: {row.get('status')}")
        out.append(
            f"- retrieved: {_ids(sources)} · cited: {_ids(response.get('cited_source_ids') or [])}"
            f" · invalid citations: {_ids(response.get('invalid_citations') or [])}"
        )
        out.append(f"- deterministic checks: {_score_line(row.get('scores') or [])}")
        out += ["", "**Answer**", "", _quote(str(response.get("answer") or ""))]
    else:
        error = row.get("error") or {}
        out.append(f"- not answered: {row.get('status')} ({error.get('code')}: {error.get('message')}) — mark n/a")
    out += ["", "**Review** (tick exactly one box per line)", ""]
    for key, question in CRITERIA:
        out.append(f"- {key}: [ ] yes [ ] no [ ] n/a — {question}")
    out += ["", "Reviewer notes: <!-- write below this line -->", "", "", "---", ""]
    return out


def render_worksheet(rows: Iterable[Mapping[str, Any]], *, header: Mapping[str, Any]) -> str:
    """A markdown worksheet for every row whose case needs human review."""
    selected = [r for r in rows if needs_review(r)]
    context = " · ".join(f"{k} `{v}`" for k, v in header.items() if v is not None)
    lines = [
        "# Ask Pivot — human review worksheet",
        "",
        context + " · data: synthetic_demo (fictional league)",
        "",
        "The deterministic scorers check numbers, directions, citations and refusals. They cannot",
        "judge whether a recommendation is *good*: whether it weighs what each option costs, argues",
        "against its own lean, and hands the GM the call that is the GM's. That is this worksheet.",
        "",
        "For each case: read the answer against the rubric, replace exactly one `[ ]` per line",
        "with `[x]`, and write notes under *Reviewer notes*. Then count the result with",
        "`cd backend && .venv/bin/python -m scripts.eval_rag review --tally <this file>`.",
        "",
        f"Cases to review: {len(selected)} (categories: {', '.join(REVIEW_CATEGORIES)}).",
        "",
        "---",
        "",
    ]
    if not selected:
        lines += ["No case in this run needs human review (none of the categories above was run).", ""]
    for row in selected:
        lines += _case_section(row)
    return "\n".join(lines)


def write_worksheet(run_dir: Path, *, header: Mapping[str, Any], force: bool = False) -> tuple[Path, bool]:
    """(path, written). An existing worksheet is never overwritten unless `force`: it may
    hold a reviewer's work."""
    path = run_dir / WORKSHEET_NAME
    if path.exists() and not force:
        return path, False
    rows = read_results(run_dir / RESULTS_NAME)
    path.write_text(render_worksheet(rows, header=header), encoding="utf-8")
    return path, True


# ------------------------------------------------------------------------------ parsing


@dataclass
class WorksheetTally:
    """What a filled worksheet says, counted. `unreviewed` = no box ticked; `invalid` =
    more than one box ticked on a line."""

    cases: int = 0
    criteria: dict[str, dict[str, int]] = field(default_factory=dict)
    per_case: dict[str, dict[str, str]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.cases > 0 and all(c["unreviewed"] == 0 and c["invalid"] == 0 for c in self.criteria.values())

    def as_dict(self) -> dict[str, Any]:
        return {"cases": self.cases, "complete": self.complete, "criteria": self.criteria,
                "per_case": self.per_case, "notes": self.notes}


def _verdict(match: re.Match[str]) -> str:
    ticked = [v for v, group in zip(VERDICTS, ("yes", "no", "na"), strict=True) if match.group(group) in "xX"]
    if not ticked:
        return "unreviewed"
    return ticked[0] if len(ticked) == 1 else "invalid"


def parse_worksheet(text: str) -> WorksheetTally:
    """Count a filled worksheet: per criterion, how many cases are yes / no / n-a /
    unreviewed / invalid; plus each case's verdicts and non-empty notes."""
    tally = WorksheetTally(criteria={key: dict.fromkeys((*VERDICTS, "unreviewed", "invalid"), 0) for key, _ in CRITERIA})
    case_id: str | None = None
    notes: list[str] | None = None

    def close_notes() -> None:
        if case_id is not None and notes is not None:
            text_notes = _COMMENT.sub("", "\n".join(notes)).strip()
            if text_notes:
                tally.notes[case_id] = text_notes

    for line in text.splitlines():
        heading = _CASE_HEADING.match(line)
        if heading:
            close_notes()
            case_id, notes = heading.group("id"), None
            tally.cases += 1
            tally.per_case[case_id] = {}
            continue
        if case_id is None:
            continue
        if line.strip() == "---":
            close_notes()
            notes = None
            continue
        criterion = _CRITERION_LINE.match(line)
        if criterion and criterion.group("key") in tally.criteria:
            verdict = _verdict(criterion)
            tally.criteria[criterion.group("key")][verdict] += 1
            tally.per_case[case_id][criterion.group("key")] = verdict
            continue
        note_start = _NOTES_LINE.match(line)
        if note_start:
            notes = [note_start.group("rest")]
            continue
        if notes is not None:
            notes.append(line)
    close_notes()
    # A criterion line deleted from a section counts as unreviewed, not as silently absent.
    for verdicts in tally.per_case.values():
        for key, _ in CRITERIA:
            if key not in verdicts:
                verdicts[key] = "unreviewed"
                tally.criteria[key]["unreviewed"] += 1
    return tally


def format_tally(tally: WorksheetTally) -> str:
    lines = [f"Reviewed cases: {tally.cases}" + ("" if tally.complete else " (incomplete)"), ""]
    lines.append(f"{'criterion':<22}{'yes':>5}{'no':>5}{'n/a':>5}{'unreviewed':>12}{'invalid':>9}")
    for key, counts in tally.criteria.items():
        lines.append(
            f"{key:<22}{counts['yes']:>5}{counts['no']:>5}{counts['n/a']:>5}{counts['unreviewed']:>12}{counts['invalid']:>9}"
        )
    if tally.notes:
        lines += ["", "Notes:"]
        lines += [f"- {case}: {note.splitlines()[0][:160]}" for case, note in tally.notes.items()]
    return "\n".join(lines)
