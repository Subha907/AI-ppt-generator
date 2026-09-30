"""Fetch and pretty-print an architecture model from S3 for inspection."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.aws import s3_client  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.store import model_object_key  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--tenant", default=None)
    args = p.parse_args()

    s = get_settings()
    tenant = args.tenant or s.local_tenant_id
    key = model_object_key(tenant, args.project, args.job)
    obj = s3_client().get_object(Bucket=s.data_bucket, Key=key)
    m = json.loads(obj["Body"].read())

    print("title:", m["title"])
    print("type:", m["architectureType"])
    print("qualityAttributes:", m.get("qualityAttributes"))
    print("assumptions:", len(m.get("assumptions", [])))
    print("\nNODES:")
    for n in m["nodes"]:
        svc = (n.get("properties") or {}).get("awsService")
        print(
            f"  {n['id']:16} {n['type']:16} {n['label'][:32]:32} "
            f"parent={n.get('parentId')} svc={svc} ev={n.get('evidenceIds')}"
        )
    print("\nEDGES:")
    for e in m["edges"]:
        print(f"  {e['id']:6} {e['source']} -> {e['target']} "
              f"({e.get('protocol')}) {e.get('label')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
