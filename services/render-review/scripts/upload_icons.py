"""Upload AWS Architecture Icons into the shared S3 reference bucket.

You download the official "Icon package" ZIP from
https://aws.amazon.com/architecture/icons/ and extract it locally. This script
scans that extracted folder, finds the PNG for each icon we need (by fuzzy
matching the service name), and uploads it to S3 under a clean, versioned key:

    s3://{ICON_BUCKET}/aws-icons/{version}/{category}/{service}.png

Only the local extract is temporary - S3 is the source of truth. Agent 3 then
reads icons from S3 by iconKey (e.g. "compute/ecs").

Usage:
    python -m scripts.upload_icons --src "C:\\path\\to\\extracted\\Asset-Package_..."
    python -m scripts.upload_icons --src "..." --version v2025 --dry-run
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.aws import s3_client  # noqa: E402
from app.config import get_settings  # noqa: E402

# iconKey (category/service) -> list of filename keywords to match in the AWS
# package. The AWS package names files like
# "Arch_Amazon-Elastic-Container-Service_48.png" or "Res_..._48.png".
ICON_MATCHERS: dict[str, list[str]] = {
    "compute/ecs": ["elastic-container-service", "ecs"],
    "compute/eks": ["elastic-kubernetes-service", "eks"],
    "compute/lambda": ["lambda"],
    "compute/ec2": ["ec2"],
    "compute/generic-compute": ["ec2"],
    "database/rds": ["rds"],
    "database/dynamodb": ["dynamodb"],
    "database/aurora": ["aurora"],
    "database/generic-database": ["rds"],
    "storage/s3": ["simple-storage-service", "s3"],
    "storage/generic-storage": ["simple-storage-service", "s3"],
    "integration/sqs": ["simple-queue-service", "amazon-sqs"],
    "integration/sns": ["simple-notification-service", "amazon-sns"],
    "integration/eventbridge": ["eventbridge"],
    "integration/generic-integration": ["simple-queue-service"],
    "network/cloudfront": ["cloudfront"],
    "network/elb": ["elastic-load-balancing"],
    "network/api-gateway": ["api-gateway"],
    "network/generic-network": ["virtual-private-cloud", "amazon-vpc"],
    "network/edge": ["cloudfront"],
    "security/waf": ["waf"],
    "security/iam": ["identity-and-access-management", "iam"],
    "security/secrets-manager": ["secrets-manager"],
    "security/kms": ["key-management-service", "kms"],
    "security/cognito": ["cognito"],
    "security/generic-security": ["identity-and-access-management", "iam"],
    "management/cloudwatch": ["cloudwatch"],
    "management/cloudtrail": ["cloudtrail"],
    "management/config": ["aws-config", "config"],
    "management/xray": ["x-ray"],
    "management/observability": ["cloudwatch"],
    "security/acm": ["certificate-manager"],
    "network/route53": ["route-53", "route53"],
}


def _find_png(root: pathlib.Path, keywords: list[str], prefer_size: str = "48") -> pathlib.Path | None:
    """Find the best-matching PNG under root for the keywords.

    Keywords are tried in priority order; within a keyword we prefer the file
    whose base name (minus 'Arch_' prefix and size suffix) most closely equals
    the keyword, so 'amazon-ecs' beats 'amazon-ecs-anywhere'.
    """
    all_png = [p for p in root.rglob("*.png") if "@" not in p.name]

    def base_service(name: str) -> str:
        # Strip Arch_/Res_ prefix and _64.png style suffix, normalize.
        n = name
        for pre in ("Arch_", "Res_", "Arch-Category_"):
            if n.startswith(pre):
                n = n[len(pre):]
        n = n.rsplit("_", 1)[0]  # drop size
        return n.lower().replace("-", "").replace("_", "").replace("amazon", "").replace("aws", "")

    for kw in keywords:
        kw_norm = kw.lower().replace("-", "").replace("_", "").replace("amazon", "").replace("aws", "")
        matches = [p for p in all_png if kw_norm in base_service(p.name)]
        if not matches:
            continue
        # Prefer exact base-name equality, then shortest name, then size.
        def score(p: pathlib.Path) -> tuple:
            bs = base_service(p.name)
            exact = 0 if bs == kw_norm else 1
            sized = 0 if f"_{prefer_size}." in p.name else 1
            return (exact, len(bs), sized, len(p.name))
        matches.sort(key=score)
        return matches[0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, help="path to the extracted AWS icon package")
    parser.add_argument("--version", default="v2025")
    parser.add_argument("--size", default="48")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = pathlib.Path(args.src)
    if not root.exists():
        print(f"Source folder not found: {root}", file=sys.stderr)
        return 1

    settings = get_settings()
    bucket = settings.icon_bucket
    if not bucket:
        print("ICON_BUCKET not set in .env", file=sys.stderr)
        return 1

    s3 = s3_client()
    uploaded, missing = 0, []
    for icon_key, keywords in ICON_MATCHERS.items():
        png = _find_png(root, keywords, args.size)
        if not png:
            missing.append(icon_key)
            continue
        s3_key = f"aws-icons/{args.version}/{icon_key}.png"
        if args.dry_run:
            print(f"[dry-run] {icon_key:34} <- {png.name}")
        else:
            s3.upload_file(str(png), bucket, s3_key, ExtraArgs={"ContentType": "image/png"})
            print(f"uploaded {icon_key:34} <- {png.name}")
        uploaded += 1

    print(f"\n{uploaded} icons {'matched' if args.dry_run else 'uploaded'} to "
          f"s3://{bucket}/aws-icons/{args.version}/")
    if missing:
        print(f"NOT FOUND ({len(missing)}): {', '.join(missing)}")
        print("(these will fall back to colored shapes in Agent 3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
