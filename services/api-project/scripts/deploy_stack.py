"""Deploy the API/Project infrastructure via boto3, in two stacks.

Why two stacks: Bedrock Knowledge Base early-validation requires the S3 Vectors
index to already exist before the KB can reference it. So the base resources
(buckets, index, table, role) deploy first, then the KB stack imports their
exported ARNs.

Why boto3 and not the AWS CLI: the locally installed AWS CLI (v2.0.30) predates
Bedrock/S3-Vectors; the pinned boto3 supports CloudFormation for them.

Usage:
    python -m scripts.deploy_stack               # deploy both stacks
    python -m scripts.deploy_stack --write-env   # also patch .env with outputs
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.aws import _session  # noqa: E402
from app.config import get_settings  # noqa: E402

CFN_DIR = (
    pathlib.Path(__file__).resolve().parents[3] / "infrastructure" / "cloudformation"
)
BASE_TEMPLATE = CFN_DIR / "01-base-stack.yaml"
KB_TEMPLATE = CFN_DIR / "02-kb-stack.yaml"

BASE_STACK = "slidegen-base"
KB_STACK = "slidegen-kb"

ENV_PATH = pathlib.Path(__file__).resolve().parents[1] / ".env"


def _deploy(cfn, stack_name: str, template_path: pathlib.Path) -> dict[str, str]:
    body = template_path.read_text(encoding="utf-8")
    common = dict(
        StackName=stack_name,
        TemplateBody=body,
        Capabilities=["CAPABILITY_NAMED_IAM"],
    )
    exists = True
    try:
        cfn.describe_stacks(StackName=stack_name)
    except cfn.exceptions.ClientError:
        exists = False

    if exists:
        print(f"[{stack_name}] exists -> update")
        try:
            cfn.update_stack(**common)
            cfn.get_waiter("stack_update_complete").wait(
                StackName=stack_name, WaiterConfig={"Delay": 15, "MaxAttempts": 120}
            )
        except cfn.exceptions.ClientError as exc:
            if "No updates are to be performed" in str(exc):
                print(f"[{stack_name}] no changes")
            else:
                raise
    else:
        print(f"[{stack_name}] create")
        cfn.create_stack(**common, OnFailure="DELETE")
        cfn.get_waiter("stack_create_complete").wait(
            StackName=stack_name, WaiterConfig={"Delay": 15, "MaxAttempts": 120}
        )

    desc = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in desc.get("Outputs", [])}
    print(f"[{stack_name}] status={desc['StackStatus']}")
    for k, v in outputs.items():
        print(f"    {k} = {v}")
    return outputs


def _patch_env(env_map: dict[str, str]) -> None:
    text = ENV_PATH.read_text(encoding="utf-8")
    for key, val in env_map.items():
        pattern = rf"(?m)^{re.escape(key)}=.*$"
        replacement = f"{key}={val}"
        if re.search(pattern, text):
            text = re.sub(pattern, replacement, text)
        else:
            text += f"\n{replacement}"
    ENV_PATH.write_text(text, encoding="utf-8")
    print(f"\nPatched {ENV_PATH} with resource names.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-env", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    cfn = _session().client("cloudformation")
    print(f"Region: {settings.aws_region}  Profile: {settings.aws_profile}\n")

    base = _deploy(cfn, BASE_STACK, BASE_TEMPLATE)
    print()
    kb = _deploy(cfn, KB_STACK, KB_TEMPLATE)

    env_map = {
        "DATA_BUCKET": base.get("DataBucketName", ""),
        "VECTOR_BUCKET": base.get("VectorBucketName", ""),
        "DDB_TABLE": base.get("DynamoTableName", "ArchitectureGenerator"),
        "KNOWLEDGE_BASE_ID": kb.get("KnowledgeBaseId", ""),
        "DATA_SOURCE_ID": kb.get("DataSourceId", ""),
    }

    print("\n=== Values for services/api-project/.env ===")
    for k, v in env_map.items():
        print(f"{k}={v}")

    if args.write_env:
        _patch_env(env_map)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
