"""CLI trigger for Agent 4 - Visual Reviewer.

Usage:
    python -m app.review_run --project <projectId> --job <jobId>
    python -m app.review_run --project <p> --job <j> --attempt 16 --provider nova
"""
from __future__ import annotations

import argparse
import json
import sys

from .aws import SSOAuthError, verify_sso
from .review_agent import review_from_job
from .review_validation import ReviewValidationFailure
from .store import NotFoundError


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 4 - Visual Reviewer")
    parser.add_argument("--project", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--provider", default=None, choices=["stub", "nova"])
    args = parser.parse_args()

    try:
        who = verify_sso()
        print(f"AWS: {who['arn']} ({who['region']})")
    except SSOAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        result = review_from_job(
            args.project, args.job, args.tenant, args.attempt, args.provider
        )
    except NotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ReviewValidationFailure as exc:
        print("REVIEW REPORT INVALID:", file=sys.stderr)
        for e in exc.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(json.dumps({
        "decision": result.decision,
        "score": result.score,
        "issue_count": result.issue_count,
        "provider": result.provider,
        "report_s3_uri": result.report_s3_uri,
        "issues": result.issues,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
