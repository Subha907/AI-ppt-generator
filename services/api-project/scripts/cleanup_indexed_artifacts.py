"""Remove pipeline artifacts that were mistakenly stored under the KB-scanned
"tenants/.../jobs/..." tree and re-sync the knowledge base.

Going forward the services write artifacts under "artifacts/..." (outside the
KB inclusion prefix), so only RFP source files are indexed. This one-off script
cleans up the already-misindexed objects: it deletes S3 objects matching
tenants/*/projects/*/jobs/* (the models/reviews/outputs/previews), then starts
a KB ingestion job so the vector index drops those deleted objects.

Dry-run by default; pass --apply to actually delete + resync.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.aws import bedrock_agent_client, s3_client  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually delete + resync")
    args = parser.parse_args()

    s = get_settings()
    s3 = s3_client()
    bucket = s.data_bucket

    # Find all objects under tenants/ that are inside a /jobs/ folder.
    to_delete: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="tenants/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "/jobs/" in key:
                to_delete.append(key)

    print(f"Found {len(to_delete)} artifact objects wrongly under tenants/.../jobs/")
    for k in to_delete[:20]:
        print("  ", k)
    if len(to_delete) > 20:
        print(f"  ... and {len(to_delete) - 20} more")

    if not args.apply:
        print("\n(dry-run) Pass --apply to delete these and re-sync the KB.")
        return 0

    # Delete in batches of 1000.
    for i in range(0, len(to_delete), 1000):
        batch = [{"Key": k} for k in to_delete[i : i + 1000]]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": batch})
    print(f"Deleted {len(to_delete)} objects.")

    # Re-sync the KB so the vector index drops the deleted objects.
    resp = bedrock_agent_client().start_ingestion_job(
        knowledgeBaseId=s.knowledge_base_id,
        dataSourceId=s.data_source_id,
        description="Cleanup: drop wrongly-indexed job artifacts",
    )
    print("Started re-sync ingestion job:", resp["ingestionJob"]["ingestionJobId"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
