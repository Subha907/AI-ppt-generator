"""List jobs and which pipeline artifacts they already have, so you know what
is testable with the Compiler (Agent 3). A job is compilable once it has a
layout-model.json (Agent 2 done).
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

    projects = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={":pk": f"TENANT#{tenant}", ":sk": "PROJECT#"},
    ).get("Items", [])

    any_job = False
    for p in projects:
        pid = p["projectId"]
        child = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"PROJECT#{pid}"},
        ).get("Items", [])
        for j in [c for c in child if c.get("entity") == "JOB"]:
            any_job = True
            jid = j["jobId"]
            base = f"tenants/{tenant}/projects/{pid}/jobs/{jid}"
            has_arch = _exists(s.data_bucket, f"{base}/models/architecture-model.json")
            has_layout = _exists(s.data_bucket, f"{base}/models/layout-model.json")
            has_pptx = _exists(s.data_bucket, f"{base}/outputs/version-1/architecture.pptx")
            print(f"PROJECT {pid}")
            print(f"  JOB {jid}  type={j.get('architectureType')} status={j.get('status')}")
            print(f"     architecture-model.json: {'YES' if has_arch else 'no'}")
            print(f"     layout-model.json      : {'YES' if has_layout else 'no'}")
            print(f"     architecture.pptx      : {'YES' if has_pptx else 'no'}")
            if has_layout:
                print(f"     -> COMPILABLE with Agent 3: project={pid} job={jid}")
    if not any_job:
        print("No jobs found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
