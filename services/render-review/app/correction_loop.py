"""Bounded visual-review correction loop (Phase F).

Keeps Agent 4 a REVIEWER, not an unbounded redesign agent. This module drives a
deterministic, capped loop:

    compile/preview -> review -> (PASS? stop) -> translate issues to bounded
    layout constraints -> re-layout -> repeat, up to a hard iteration cap.

Design guarantees (from the improvement plan):
  * BOUNDED: at most `max_iterations` review cycles; always terminates.
  * DETERMINISTIC: issue -> constraint translation is a fixed mapping; no
    nondeterministic redesign.
  * HONEST: a stub/unavailable reviewer is reported as such - a stub PASS is
    never presented as a real visual approval.
  * TRACEABLE: every iteration (decision, score, issues, applied corrections) is
    recorded and returned.
  * NON-DESTRUCTIVE: this module never rasterizes the editable PPTX; it only
    produces layout CONSTRAINTS that Agent 2 applies deterministically.

It takes callables so it is unit-testable without AWS or cross-service calls:
  review_fn(attempt) -> report dict (the review-model contract shape)
  relayout_fn(constraints, attempt) -> None (re-run compile+layout for next pass)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# Review issue rule -> a bounded, deterministic layout constraint action. These
# are advisory nudges Agent 2 can apply; they never invent geometry here.
_RULE_TO_ACTION: dict[str, str] = {
    "OVERLAP": "increase_spacing",
    "INCONSISTENT_SPACING": "normalize_spacing",
    "TEXT_CLIPPED": "enlarge_label_box",
    "UNREADABLE_LABEL": "enlarge_icons",
    "LOW_CONTRAST": "restyle_contrast",
    "OUT_OF_BOUNDS": "shrink_to_fit",
    "EXCESSIVE_CROSSINGS": "reroute_connectors",
    "EMPTY_GROUP": "drop_empty_group",
}


@dataclass
class IterationRecord:
    attempt: int
    provider: str
    decision: str
    score: int
    issue_count: int
    applied_constraints: list[dict] = field(default_factory=list)


@dataclass
class LoopResult:
    final_decision: str          # PASS | REVISE | ERROR
    final_score: int
    iterations: int
    stopped_reason: str          # 'passed' | 'iteration_cap' | 'no_progress' | 'stub'
    reviewer_is_stub: bool
    history: list[IterationRecord] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def issues_to_constraints(issues: list[dict]) -> list[dict]:
    """Translate review issues into bounded, deterministic layout constraints.

    Each constraint is {action, objectIds, delta?} - an instruction Agent 2 can
    apply deterministically. Unknown rules are passed through as a generic
    'review_hint' so nothing is silently dropped.
    """
    constraints: list[dict] = []
    for issue in issues:
        rule = (issue.get("rule") or "").upper()
        action = _RULE_TO_ACTION.get(rule, "review_hint")
        constraint = {
            "action": action,
            "rule": rule,
            "objectIds": list(issue.get("objectIds") or []),
            "severity": issue.get("severity", "MEDIUM"),
        }
        rec = issue.get("recommendedAction") or {}
        if isinstance(rec, dict) and rec.get("delta"):
            constraint["delta"] = rec["delta"]
        constraints.append(constraint)
    return constraints


def run_correction_loop(
    review_fn: Callable[[int], dict],
    relayout_fn: Callable[[list[dict], int], None],
    *,
    max_iterations: int = 3,
    pass_decision: str = "PASS",
) -> LoopResult:
    """Drive the bounded review->relayout loop.

    review_fn(attempt) returns a review report dict (review-model shape) and may
    include a truthy 'isStub' field when the reviewer is a stub/unavailable.
    relayout_fn(constraints, next_attempt) re-runs compile+layout with the given
    bounded constraints before the next review.
    """
    history: list[IterationRecord] = []
    diagnostics: list[str] = []
    reviewer_is_stub = False
    prev_score: int | None = None

    for attempt in range(1, max_iterations + 1):
        try:
            report = review_fn(attempt)
        except Exception as exc:  # noqa: BLE001 - surface honestly, do not loop
            diagnostics.append(f"review failed on attempt {attempt}: {exc}")
            return LoopResult(
                final_decision="ERROR", final_score=0, iterations=attempt,
                stopped_reason="error", reviewer_is_stub=reviewer_is_stub,
                history=history, diagnostics=diagnostics,
            )

        is_stub = bool(report.get("isStub") or report.get("provider") == "stub")
        reviewer_is_stub = reviewer_is_stub or is_stub
        decision = report.get("decision", "REVISE")
        score = int(report.get("score", 0))
        issues = report.get("issues", []) or []
        constraints = issues_to_constraints(issues)

        history.append(IterationRecord(
            attempt=attempt, provider=report.get("provider", "unknown"),
            decision=decision, score=score, issue_count=len(issues),
            applied_constraints=constraints,
        ))

        if decision == pass_decision:
            if is_stub:
                diagnostics.append(
                    "reviewer is a STUB - PASS is NOT a real visual approval"
                )
                return LoopResult(
                    final_decision=decision, final_score=score, iterations=attempt,
                    stopped_reason="stub", reviewer_is_stub=True,
                    history=history, diagnostics=diagnostics,
                )
            diagnostics.append(f"passed on attempt {attempt} (score {score})")
            return LoopResult(
                final_decision=decision, final_score=score, iterations=attempt,
                stopped_reason="passed", reviewer_is_stub=reviewer_is_stub,
                history=history, diagnostics=diagnostics,
            )

        # Not passed. Stop if we've hit the cap or made no measurable progress
        # (avoids oscillating corrections).
        if attempt >= max_iterations:
            diagnostics.append(
                f"iteration cap ({max_iterations}) reached without PASS"
            )
            return LoopResult(
                final_decision=decision, final_score=score, iterations=attempt,
                stopped_reason="iteration_cap", reviewer_is_stub=reviewer_is_stub,
                history=history, diagnostics=diagnostics,
            )
        if prev_score is not None and score <= prev_score:
            diagnostics.append(
                f"no score improvement ({prev_score} -> {score}); stopping to "
                "avoid oscillation"
            )
            return LoopResult(
                final_decision=decision, final_score=score, iterations=attempt,
                stopped_reason="no_progress", reviewer_is_stub=reviewer_is_stub,
                history=history, diagnostics=diagnostics,
            )
        prev_score = score

        # Apply bounded corrections and re-layout for the next attempt.
        try:
            relayout_fn(constraints, attempt + 1)
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(f"re-layout failed after attempt {attempt}: {exc}")
            return LoopResult(
                final_decision=decision, final_score=score, iterations=attempt,
                stopped_reason="error", reviewer_is_stub=reviewer_is_stub,
                history=history, diagnostics=diagnostics,
            )

    # Unreachable (loop returns inside), but keep a safe default.
    return LoopResult(
        final_decision="REVISE", final_score=0, iterations=max_iterations,
        stopped_reason="iteration_cap", reviewer_is_stub=reviewer_is_stub,
        history=history, diagnostics=diagnostics,
    )
