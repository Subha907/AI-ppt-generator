"""Map architecture node types (and AWS service names) to icon manifest keys.

Agent 3 (Compiler) resolves these keys against the shared AWS icon bucket. Here
we only need to assign a stable key string per node so the layout is renderable.
"""
from __future__ import annotations

# Node type -> default icon key.
_TYPE_ICON = {
    "COMPUTE": "compute/generic-compute",
    "DATABASE": "database/generic-database",
    "STORAGE": "storage/generic-storage",
    "NETWORK": "network/generic-network",
    "INTEGRATION": "integration/generic-integration",
    "EDGE": "network/edge",
    "SECURITY": "security/generic-security",
    "OBSERVABILITY": "management/observability",
    "EXTERNAL": "general/external",
    "ACTOR": "general/user",
    "SERVICE": "general/service",
    "COMPONENT": "general/component",
    "PROCESS": "general/process",
    "CAPABILITY": "general/capability",
    "DATASTORE": "storage/generic-storage",
}

# Keyword (found in the AWS service name) -> specific icon key. Matched by
# substring on a normalized (lowercased, punctuation-stripped) service name, so
# both "AWS IAM" and "AWS Identity and Access Management" resolve correctly.
# Order matters: more specific keywords first.
_SERVICE_KEYWORDS: list[tuple[str, str]] = [
    ("elasticcontainerservice", "compute/ecs"),
    ("ecs", "compute/ecs"),
    ("elastickubernetes", "compute/eks"),
    ("eks", "compute/eks"),
    ("lambda", "compute/lambda"),
    ("ec2", "compute/ec2"),
    ("relationaldatabase", "database/rds"),
    ("rds", "database/rds"),
    ("dynamodb", "database/dynamodb"),
    ("aurora", "database/aurora"),
    ("simplestorage", "storage/s3"),
    ("s3", "storage/s3"),
    ("simplequeue", "integration/sqs"),
    ("sqs", "integration/sqs"),
    ("simplenotification", "integration/sns"),
    ("sns", "integration/sns"),
    ("eventbridge", "integration/eventbridge"),
    ("cloudfront", "network/cloudfront"),
    ("elasticloadbalancing", "network/elb"),
    ("loadbalancer", "network/elb"),
    ("apigateway", "network/api-gateway"),
    ("route53", "network/route53"),
    ("webapplicationfirewall", "security/waf"),
    ("waf", "security/waf"),
    ("identityandaccess", "security/iam"),
    ("iam", "security/iam"),
    ("secretsmanager", "security/secrets-manager"),
    ("keymanagement", "security/kms"),
    ("kms", "security/kms"),
    ("certificatemanager", "security/acm"),
    ("acm", "security/acm"),
    ("cognito", "security/cognito"),
    ("cloudwatch", "management/cloudwatch"),
    ("cloudtrail", "management/cloudtrail"),
    ("xray", "management/xray"),
    ("config", "management/config"),
]

# Container-ish node types render as grouping boxes, not icons.
CONTAINER_TYPES = {
    "CONTAINER", "ACCOUNT", "REGION", "VPC", "AVAILABILITYZONE", "SUBNET",
}


def _normalize(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def icon_key(node_type: str, aws_service: str | None) -> str | None:
    if node_type in CONTAINER_TYPES:
        return None
    # Prefer a specific icon matched from the AWS service name.
    if aws_service:
        norm = _normalize(aws_service)
        for kw, key in _SERVICE_KEYWORDS:
            if kw in norm:
                return key
    return _TYPE_ICON.get(node_type, "general/component")
