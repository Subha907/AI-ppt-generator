"""Validate the review report against the contract + Agent 4 gates (PDF 5.4/5.5).

Gates:
  - Report conforms to the JSON schema.
  - Each issue identifies objectIds.
  - Severity and recommendedAction are present (enforced by schema).
  - decision is PASS or REVISE.
"""
from __future__ import annotations

import json
import pathlib

from jsonschema import Draft202012Validator

CONTRACT_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "review_model.contract.json"
)


class ReviewValidationFailure(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def validate_report(report: dict) -> None:
    validator = Draft202012Validator(_load_contract())
    errors = []
    for e in sorted(validator.iter_errors(report), key=lambda x: list(x.path)):
        loc = "/".join(str(p) for p in e.path) or "(root)"
        errors.append(f"schema: {loc}: {e.message}")

    # Extra semantic gate: every issue must cite at least one objectId.
    if not errors:
        for i, issue in enumerate(report.get("issues", [])):
            if not issue.get("objectIds"):
                errors.append(f"issue[{i}] '{issue.get('issueId')}' has no objectIds")

    if errors:
        raise ReviewValidationFailure(errors)
