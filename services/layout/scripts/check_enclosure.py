"""Check that every child object sits inside its parent container."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.aws import s3_client  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--job", required=True)
    args = p.parse_args()
    s = get_settings()
    key = (
        f"tenants/{s.local_tenant_id}/projects/{args.project}"
        f"/jobs/{args.job}/models/layout-model.json"
    )
    m = json.loads(s3_client().get_object(Bucket=s.data_bucket, Key=key)["Body"].read())
    byid = {o["objectId"]: o for o in m["objects"]}
    eps = 0.02
    issues = 0
    for o in m["objects"]:
        pid = o.get("parentObjectId")
        if not pid or pid not in byid:
            continue
        par = byid[pid]
        if (
            o["x"] < par["x"] - eps
            or o["y"] < par["y"] - eps
            or o["x"] + o["w"] > par["x"] + par["w"] + eps
            or o["y"] + o["h"] > par["y"] + par["h"] + eps
        ):
            print(f"OUTSIDE: {o['objectId']} not inside {pid}")
            issues += 1
    print("enclosure violations:", issues)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
