"""Knowledge Base retrieval, scoped to a project and its selected documents.

Tenant/project isolation is enforced by filtering on the S3 source URI prefix.
RFP objects live at:
  s3://{dataBucket}/tenants/{tenantId}/projects/{projectId}/rfps/{documentId}/source/...
so filtering retrieved chunks on that URI prefix guarantees a caller can only
ever see evidence from their own tenant + project, and only from the documents
they selected for the job. The user instruction cannot widen this scope.
"""
from __future__ import annotations

from dataclasses import dataclass

from .aws import bedrock_agent_runtime_client
from .config import get_settings


@dataclass
class Evidence:
    """A retrieved chunk with a stable, model-facing evidence id."""

    evidence_id: str  # e.g. "ev-1"
    text: str
    source_uri: str
    document_id: str
    score: float


def _project_prefix(tenant_id: str, project_id: str) -> str:
    settings = get_settings()
    return (
        f"s3://{settings.data_bucket}/tenants/{tenant_id}"
        f"/projects/{project_id}/rfps/"
    )


def _document_id_from_uri(uri: str) -> str:
    """Extract the documentId segment from an RFP source URI."""
    marker = "/rfps/"
    if marker in uri:
        rest = uri.split(marker, 1)[1]
        return rest.split("/", 1)[0]
    return ""


def retrieve(
    tenant_id: str,
    project_id: str,
    document_ids: list[str],
    query: str,
) -> list[Evidence]:
    """Retrieve evidence chunks for the query, scoped to project + documents.

    Returns evidence with stable ids ev-1, ev-2, ... in relevance order.
    """
    settings = get_settings()
    settings.require("knowledge_base_id", "data_bucket")

    client = bedrock_agent_runtime_client()
    resp = client.retrieve(
        knowledgeBaseId=settings.knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": settings.retrieval_max_results
            }
        },
    )

    project_prefix = _project_prefix(tenant_id, project_id)
    wanted = set(document_ids)

    evidence: list[Evidence] = []
    for item in resp.get("retrievalResults", []):
        location = item.get("location", {})
        uri = (location.get("s3Location") or {}).get("uri", "")

        # Hard scope check: chunk must belong to this tenant+project.
        if not uri.startswith(project_prefix):
            continue

        doc_id = _document_id_from_uri(uri)
        # Only include documents the job selected.
        if wanted and doc_id not in wanted:
            continue

        text = (item.get("content") or {}).get("text", "")
        if not text.strip():
            continue

        evidence.append(
            Evidence(
                evidence_id=f"ev-{len(evidence) + 1}",
                text=text,
                source_uri=uri,
                document_id=doc_id,
                score=float(item.get("score", 0.0)),
            )
        )

    return evidence
