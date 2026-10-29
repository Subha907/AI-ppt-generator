"""CLI trigger for Agent 2 - run the layout engine standalone against a job.

Usage:
    python -m app.run --project <projectId> --job <jobId>
"""
from __future__ import annotations

import argparse
import json
import sys

from .agent import run_from_job
from .aws import SSOAuthError, verify_sso
from .store import NotFoundError
from .validation import ValidationFailure


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 2 - Layout & Geometric")
    parser.add_argument("--project", required=True, help="projectId (prj_...)")
    parser.add_argument("--job", required=True, help="jobId (job_...)")
    parser.add_argument("--tenant", default=None)
    parser.add_argument(
        "--engine", default=None,
        choices=["stack", "stack-abs", "graphviz", "grid"],
        help="explicit layout engine override. Omit to let the policy resolver "
             "choose from the model's rendererHints.preferredLayout. "
             "stack = AWS-style recursive grids; stack-abs = absolute swimlanes "
             "(multi-AZ); graphviz = auto-routing; grid = original fallback",
    )
    args = parser.parse_args()

    try:
        who = verify_sso()
        print(f"AWS: {who['arn']} ({who['region']})")
    except SSOAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        result = run_from_job(args.project, args.job, args.tenant, engine=args.engine)
    except NotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValidationFailure as exc:
        print("LAYOUT VALIDATION FAILED:", file=sys.stderr)
        for e in exc.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(json.dumps(result.__dict__, indent=2))
    print(f"\nLayout model written to: {result.layout_s3_uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
