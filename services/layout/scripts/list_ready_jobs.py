"""List jobs and whether they have an architecture-model.json (Agent 1 done)
and/or a layout-model.json (Agent 2 done), so you know what to test.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.aws import dynamodb_resource, s3_client  # noqa: E402
from app.config import get_settings  # noqa: E402


def _exists(bucket: str, key: str) -> bool:
    try:
        s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def main() -> int:
    s = get_settings()
    table = dynamodb_resource().Table(s.ddb_table)
    tenant = s.local_tenant_id
    tenant_pk = f"TENANT#{tenant}"

    projects = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={":pk": tenant_pk, ":sk": "PROJECT#"},
    ).get("Items", [])

    found_any = False
    for p in projects:
        pid = p["projectId"]
        child = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"PROJECT#{pid}"},
        ).get("Items", [])
        jobs = [c for c in child if c.get("entity") == "JOB"]
        for j in jobs:
            found_any = True
            jid = j["jobId"]
            base = f"tenants/{tenant}/projects/{pid}/jobs/{jid}/models"
            has_arch = _exists(s.data_bucket, f"{base}/architecture-model.json")
            has_layout = _exists(s.data_bucket, f"{base}/layout-model.json")
            print(f"PROJECT {pid}")
            print(f"  JOB {jid}  type={j.get('architectureType')} status={j.get('status')}")
            print(f"     architecture-model.json: {'YES' if has_arch else 'no'}")
            print(f"     layout-model.json      : {'YES' if has_layout else 'no'}")
            if has_arch:
                print(f"     -> testable with Layout API using project={pid} job={jid}")
    if not found_any:
        print("No jobs found. Create a slide-job (api-project) and run Agent 1 first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
