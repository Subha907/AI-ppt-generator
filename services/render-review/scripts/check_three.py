"""Confirm all three architecture PPTX files exist in S3 for the GemCompass project."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.aws import s3_client  # noqa: E402
from app.config import get_settings  # noqa: E402

JOBS = {
    "BUSINESS": "job_01M3H06H06TSBFERDK19RVYKYT",
    "APPLICATION": "job_01M3H07GB6J4Y1EEV8ZR4XA5AQ",
    "DEPLOYMENT": "job_01M3H1D4CPQ84VXP1KD93NG9ET",
}
PROJECT = "prj_01M3GZ2D2EJ8Y60MXKY6QNV5P0"


def main() -> int:
    s = get_settings()
    base = f"tenants/{s.local_tenant_id}/projects/{PROJECT}/jobs"
    for t, j in JOBS.items():
        key = f"{base}/{j}/outputs/version-1/architecture.pptx"
        h = s3_client().head_object(Bucket=s.data_bucket, Key=key)
        print(f"{t:12} {h['ContentLength'] // 1024:4} KB  s3://{s.data_bucket}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
