"""CLI trigger for Agent 1 - run it standalone against a job.

Usage:
    python -m app.run --project <projectId> --job <jobId>
    python -m app.run --project <projectId> --job <jobId> --provider stub
    python -m app.run --project <projectId> --job <jobId> --provider nova
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
    parser = argparse.ArgumentParser(description="Agent 1 - Solution Architect")
    parser.add_argument("--project", required=True, help="projectId (prj_...)")
    parser.add_argument("--job", required=True, help="jobId (job_...)")
    parser.add_argument("--tenant", default=None, help="tenantId (defaults to config)")
    parser.add_argument(
        "--provider", default=None, choices=["stub", "nova"],
        help="generation provider (defaults to config)",
    )
    args = parser.parse_args()

    try:
        who = verify_sso()
        print(f"AWS: {who['arn']} ({who['region']})")
    except SSOAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        result = run_from_job(
            project_id=args.project,
            job_id=args.job,
            tenant_id=args.tenant,
            provider=args.provider,
        )
    except NotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValidationFailure as exc:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in exc.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(json.dumps(result.__dict__, indent=2))
    print(f"\nArchitecture model written to: {result.model_s3_uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
