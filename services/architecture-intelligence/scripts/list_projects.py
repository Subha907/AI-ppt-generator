"""List existing projects, their documents, and jobs from DynamoDB.

Helps you find an already-ingested project/job to feed into Agent 1 without
re-uploading. Scans the single table for the local dev tenant.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.aws import dynamodb_resource  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    table = dynamodb_resource().Table(s.ddb_table)
    tenant_pk = f"TENANT#{s.local_tenant_id}"

    # Projects live under the tenant partition.
    projects = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={":pk": tenant_pk, ":sk": "PROJECT#"},
    ).get("Items", [])

    if not projects:
        print(f"No projects found for tenant {s.local_tenant_id}.")
        return 0

    for p in projects:
        pid = p["projectId"]
        print(f"\nPROJECT {pid}  '{p.get('name')}'  kbStatus={p.get('kbStatus')}")
        # Documents + jobs live under PROJECT#{projectId}
        child = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"PROJECT#{pid}"},
        ).get("Items", [])
        docs = [c for c in child if c.get("entity") == "DOCUMENT"]
        jobs = [c for c in child if c.get("entity") == "JOB"]
        for d in docs:
            print(f"   DOC  {d['documentId']}  {d.get('fileName')}  "
                  f"{d.get('ingestionStatus')}")
        for j in jobs:
            print(f"   JOB  {j['jobId']}  type={j.get('architectureType')}  "
                  f"status={j.get('status')}  docs={j.get('documentIds')}")
        if not jobs:
            print("   (no jobs yet - create one via POST /slide-jobs to run Agent 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
