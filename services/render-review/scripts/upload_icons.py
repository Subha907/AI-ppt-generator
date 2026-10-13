"""Mirror the full AWS Architecture Icons package into the shared S3 bucket.

You download the official "Icon package" ZIP from
https://aws.amazon.com/architecture/icons/ and extract it. This script mirrors
EVERY service and resource icon into S3 under clean, deterministic keys, then
writes a set of SEMANTIC ALIASES so the layout engine's stable iconKeys
(e.g. "compute/ecs", "general/user") resolve to a real icon.

Key scheme in S3 (source of truth):
    aws-icons/{version}/service/{category}/{service}.png     # Arch_* service icons
    aws-icons/{version}/resource/{category}/{name}.png       # Res_* resource icons
    aws-icons/{version}/{alias}.png                           # semantic aliases
                                                             # e.g. compute/ecs, general/user

The package layout this understands:
    Architecture-Service-Icons_*/Arch_<Category>/<size>/Arch_<Service>_<size>.png
    Resource-Icons_*/Res_<Category>/Res_<Name>_<size>[_Light|_Dark].png

Usage:
    python -m scripts.upload_icons --src "C:\\path\\to\\Icon-package_..." [--dry-run]
    python -m scripts.upload_icons --src "..." --version v2025 --size 48
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.aws import s3_client  # noqa: E402
from app.config import get_settings  # noqa: E402


def _slug(name: str) -> str:
    """Normalize an icon base name into a clean key segment.

    'Arch_Amazon-Elastic-Container-Service_48' -> 'elastic-container-service'
    'Res_General-Icons/Res_Client_48_Light'    -> 'client'
    """
    n = name
    # Drop known prefixes.
    for pre in ("Arch_", "Res_", "Arch-Category_", "Category_"):
        if n.startswith(pre):
            n = n[len(pre):]
    # Drop size + variant suffixes: _48, _48_Light, _48_Dark, _64 ...
    n = re.sub(r"_\d{2,3}(?:_(?:Light|Dark))?$", "", n)
    # Drop vendor words and lowercase/dash-normalize.
    n = n.lower()
    for v in ("amazon-", "aws-", "amazon_", "aws_"):
        n = n.replace(v, "")
    n = n.replace("_", "-")
    n = re.sub(r"[^a-z0-9\-]+", "-", n)
    n = re.sub(r"-+", "-", n).strip("-")
    return n


def _cat_slug(folder: str) -> str:
    """'Arch_Networking-Content-Delivery' -> 'networking-content-delivery'."""
    n = folder
    for pre in ("Arch_", "Res_"):
        if n.startswith(pre):
            n = n[len(pre):]
    return n.lower().replace("_", "-")


def _pick_variant(pngs: list[pathlib.Path]) -> pathlib.Path | None:
    """Prefer the Light variant, then no-variant, then Dark; skip SVGs."""
    pngs = [p for p in pngs if p.suffix.lower() == ".png"]
    if not pngs:
        return None
    light = [p for p in pngs if "_Light" in p.name or "-Light" in p.name]
    if light:
        return light[0]
    plain = [p for p in pngs if "_Dark" not in p.name and "-Dark" not in p.name]
    if plain:
        return plain[0]
    return pngs[0]


# --- Semantic aliases: iconKey used by the layout engine -> how to find the
#     source icon. Each value is (kind, category_slug, name_slug_match). The
#     uploader copies the matched mirrored icon to the alias key too, so e.g.
#     "general/user" and "compute/ecs" always resolve.
_ALIASES: dict[str, tuple[str, str, str]] = {
    # General / resource icons (the "AWS General Resources" set).
    "general/user": ("resource", "general-icons", "user"),
    "general/users": ("resource", "general-icons", "users"),
    "general/external": ("resource", "general-icons", "client"),
    "general/client": ("resource", "general-icons", "client"),
    "general/internet": ("resource", "general-icons", "internet-alt1"),
    "general/service": ("resource", "general-icons", "generic-application"),
    "general/component": ("resource", "general-icons", "generic-application"),
    "general/process": ("resource", "general-icons", "gear"),
    "general/capability": ("resource", "general-icons", "generic-application"),
    "general/document": ("resource", "general-icons", "document"),
    "general/database": ("resource", "general-icons", "database"),
    "general/mobile": ("resource", "general-icons", "mobile-client"),
    # Generic category fallbacks (used when no specific service is named).
    "compute/generic-compute": ("service", "compute", "ec2"),
    "database/generic-database": ("resource", "general-icons", "database"),
    "storage/generic-storage": ("service", "storage", "simple-storage-service"),
    "network/generic-network": ("service", "networking-content-delivery", "virtual-private-cloud"),
    "integration/generic-integration": ("service", "application-integration", "simple-queue-service"),
    "security/generic-security": ("service", "security-identity", "identity-and-access-management"),
    "management/observability": ("service", "management-tools", "cloudwatch"),
    "network/edge": ("service", "networking-content-delivery", "cloudfront"),
    # Specific service aliases the engine references (short key -> real icon).
    "compute/ecs": ("service", "containers", "elastic-container-service"),
    "compute/eks": ("service", "containers", "elastic-kubernetes-service"),
    "compute/lambda": ("service", "compute", "lambda"),
    "compute/ec2": ("service", "compute", "ec2"),
    "database/rds": ("service", "databases", "rds"),
    "database/dynamodb": ("service", "databases", "dynamodb"),
    "database/aurora": ("service", "databases", "aurora"),
    "storage/s3": ("service", "storage", "simple-storage-service"),
    "integration/sqs": ("service", "application-integration", "simple-queue-service"),
    "integration/sns": ("service", "application-integration", "simple-notification-service"),
    "integration/eventbridge": ("service", "application-integration", "eventbridge"),
    "network/cloudfront": ("service", "networking-content-delivery", "cloudfront"),
    "network/elb": ("service", "networking-content-delivery", "elastic-load-balancing"),
    "network/api-gateway": ("service", "networking-content-delivery", "api-gateway"),
    "network/route53": ("service", "networking-content-delivery", "route-53"),
    "security/waf": ("service", "security-identity", "waf"),
    "security/iam": ("service", "security-identity", "identity-and-access-management"),
    "security/secrets-manager": ("service", "security-identity", "secrets-manager"),
    "security/kms": ("service", "security-identity", "key-management-service"),
    "security/acm": ("service", "security-identity", "certificate-manager"),
    "security/cognito": ("service", "security-identity", "cognito"),
    "management/cloudwatch": ("service", "management-tools", "cloudwatch"),
    "management/cloudtrail": ("service", "management-tools", "cloudtrail"),
    "management/config": ("service", "management-tools", "config"),
    "management/xray": ("service", "developer-tools", "x-ray"),
    # Deployment infra extras referenced in rules.
    "network/nat-gateway": ("resource", "networking-content-delivery", "vpc-nat-gateway"),
    "network/internet-gateway": ("resource", "networking-content-delivery", "vpc-internet-gateway"),
    "network/vpc-endpoint": ("resource", "networking-content-delivery", "vpc-endpoints"),
}


def _mirror(root: pathlib.Path, version: str, size: str, dry: bool) -> dict[str, pathlib.Path]:
    """Mirror all service + resource PNGs. Returns {s3_key_without_ext: local_path}."""
    s3 = None if dry else s3_client()
    settings = get_settings()
    bucket = settings.icon_bucket
    index: dict[str, pathlib.Path] = {}
    uploaded = 0

    svc_root = next(root.glob("Architecture-Service-Icons_*"), None)
    res_root = next(root.glob("Resource-Icons_*"), None)

    # Service icons: <cat>/<size>/Arch_<svc>_<size>.png
    if svc_root:
        for cat_dir in sorted(p for p in svc_root.iterdir() if p.is_dir()):
            cat = _cat_slug(cat_dir.name)
            size_dir = cat_dir / size
            if not size_dir.is_dir():
                continue
            for png in sorted(size_dir.glob("*.png")):
                key = f"service/{cat}/{_slug(png.stem)}"
                index[key] = png
                s3_key = f"aws-icons/{version}/{key}.png"
                if not dry:
                    s3.upload_file(str(png), bucket, s3_key, ExtraArgs={"ContentType": "image/png"})
                uploaded += 1

    # Resource icons. Two layouts exist in the package:
    #   flat:        <cat>/Res_<name>_<size>[_Light|_Dark].png
    #   subfoldered: <cat>/Res_<size>_<variant>/Res_<name>_<size>_<variant>.png
    # rglob handles both. Group by slug and pick the preferred variant.
    if res_root:
        for cat_dir in sorted(p for p in res_root.iterdir() if p.is_dir()):
            cat = _cat_slug(cat_dir.name)
            groups: dict[str, list[pathlib.Path]] = {}
            for png in cat_dir.rglob(f"*_{size}*.png"):
                groups.setdefault(_slug(png.stem), []).append(png)
            for slug, pngs in groups.items():
                chosen = _pick_variant(pngs)
                if not chosen:
                    continue
                key = f"resource/{cat}/{slug}"
                index[key] = chosen
                s3_key = f"aws-icons/{version}/{key}.png"
                if not dry:
                    s3.upload_file(str(chosen), bucket, s3_key, ExtraArgs={"ContentType": "image/png"})
                uploaded += 1

    print(f"mirrored {uploaded} icons ({'dry-run' if dry else 'uploaded'})")
    return index


def _write_aliases(index: dict[str, pathlib.Path], version: str, dry: bool) -> None:
    """Upload each mirrored source icon to its semantic alias key too."""
    s3 = None if dry else s3_client()
    settings = get_settings()
    bucket = settings.icon_bucket
    hit, miss = 0, []
    for alias, (kind, cat, name) in _ALIASES.items():
        src_key = f"{kind}/{cat}/{name}"
        # Exact match first, then prefix/suffix contains.
        local = index.get(src_key)
        if local is None:
            cands = [p for k, p in index.items()
                     if k.startswith(f"{kind}/{cat}/") and name in k.split("/")[-1]]
            local = cands[0] if cands else None
        if local is None:
            miss.append(f"{alias}  (wanted {src_key})")
            continue
        s3_key = f"aws-icons/{version}/{alias}.png"
        if dry:
            print(f"[dry-run] alias {alias:34} <- {local.name}")
        else:
            s3.upload_file(str(local), bucket, s3_key, ExtraArgs={"ContentType": "image/png"})
        hit += 1
    print(f"aliases: {hit} written, {len(miss)} unresolved")
    for m in miss:
        print(f"  UNRESOLVED: {m}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to the extracted AWS icon package")
    ap.add_argument("--version", default="v2025")
    ap.add_argument("--size", default="48")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = pathlib.Path(args.src)
    if not root.exists():
        print(f"Source folder not found: {root}", file=sys.stderr)
        return 1
    settings = get_settings()
    if not settings.icon_bucket:
        print("ICON_BUCKET not set in .env", file=sys.stderr)
        return 1

    index = _mirror(root, args.version, args.size, args.dry_run)
    _write_aliases(index, args.version, args.dry_run)
    print(f"\nDone -> s3://{settings.icon_bucket}/aws-icons/{args.version}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
