"""CLI trigger for Agent 3 - compile the PPTX standalone against a job.

Usage:
    python -m app.run --project <projectId> --job <jobId>
    python -m app.run --project <projectId> --job <jobId> --download out.pptx
"""
from __future__ import annotations

import argparse
import json
import sys

from .agent import compile_from_job
from .aws import SSOAuthError, s3_client, verify_sso
from .store import NotFoundError
from .validation import ValidationFailure


def _download(s3_uri: str, dest: str) -> None:
    assert s3_uri.startswith("s3://")
    bucket, key = s3_uri[5:].split("/", 1)
    s3_client().download_file(bucket, key, dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 3 - Compiler")
    parser.add_argument("--project", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--download", default=None, help="save the pptx locally")
    parser.add_argument("--download-preview", default=None, help="save the preview png")
    args = parser.parse_args()

    try:
        who = verify_sso()
        print(f"AWS: {who['arn']} ({who['region']})")
    except SSOAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        result = compile_from_job(args.project, args.job, args.tenant)
    except NotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValidationFailure as exc:
        print("COMPILE VALIDATION FAILED:", file=sys.stderr)
        for e in exc.errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(json.dumps(result.__dict__, indent=2))
    if args.download:
        _download(result.pptx_s3_uri, args.download)
        print(f"\nDownloaded PPTX -> {args.download}")
    if args.download_preview:
        _download(result.preview_s3_uri, args.download_preview)
        print(f"Downloaded preview -> {args.download_preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
