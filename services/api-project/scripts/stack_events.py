"""Print recent CloudFormation stack events (failures first) for debugging."""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.aws import _session  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="slidegen-api-project")
    args = parser.parse_args()
    cfn = _session().client("cloudformation")
    try:
        resp = cfn.describe_stack_events(StackName=args.stack_name)
    except cfn.exceptions.ClientError as exc:
        print(f"Could not read events (stack may be deleted): {exc}")
        return 1
    for e in resp["StackEvents"][:60]:
        status = e.get("ResourceStatus", "")
        reason = e.get("ResourceStatusReason", "")
        rid = e.get("LogicalResourceId", "")
        rtype = e.get("ResourceType", "")
        print(f"{status:28} {rid:28} {rtype:34} {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
