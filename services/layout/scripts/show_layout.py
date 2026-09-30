"""Fetch and pretty-print a layout model from S3 for inspection."""
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
    p.add_argument("--tenant", default=None)
    args = p.parse_args()

    s = get_settings()
    tenant = args.tenant or s.local_tenant_id
    key = (
        f"tenants/{tenant}/projects/{args.project}"
        f"/jobs/{args.job}/models/layout-model.json"
    )
    obj = s3_client().get_object(Bucket=s.data_bucket, Key=key)
    m = json.loads(obj["Body"].read())

    sl = m["slide"]
    print(f"slide: {sl['widthIn']} x {sl['heightIn']} in, margin {sl['safeMarginIn']}")
    print(f"title: {m.get('title')}")
    print(f"validation: {m.get('validation')}")
    print("\nOBJECTS (x, y, w, h, z):")
    for o in sorted(m["objects"], key=lambda o: o["z"]):
        print(
            f"  z{o['z']} {o['objectId']:16} {o['kind']:10} "
            f"({o['x']:5.2f},{o['y']:5.2f}) {o['w']:4.2f}x{o['h']:4.2f} "
            f"parent={o.get('parentObjectId')} icon={o.get('iconKey')}"
        )
    print("\nCONNECTORS:")
    for c in m["connectors"]:
        pts = " -> ".join(f"({p['x']},{p['y']})" for p in c.get("points", []))
        print(f"  {c['edgeId']}: {c['sourceObjectId']} -> {c['targetObjectId']}  {pts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
