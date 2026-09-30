"""Bedrock Knowledge Base ingestion (PDF section 6: WAIT_FOR_KB_READY).

The KB has a single S3 data source scoped to the tenants/ prefix. Uploading an
RFP into the data bucket makes it eligible; we then trigger an ingestion job so
Bedrock chunks the file, generates Titan embeddings, and writes them to the S3
Vectors index. Ingestion is asynchronous, so we expose a status poll.
"""
from __future__ import annotations

from .aws import bedrock_agent_client
from .config import get_settings
from .models import IngestionStatus

# Bedrock ingestion-job status -> our IngestionStatus.
_STATUS_MAP = {
    "STARTING": IngestionStatus.INGESTING,
    "IN_PROGRESS": IngestionStatus.INGESTING,
    "COMPLETE": IngestionStatus.READY,
    "FAILED": IngestionStatus.FAILED,
    "STOPPING": IngestionStatus.INGESTING,
    "STOPPED": IngestionStatus.FAILED,
}


def start_ingestion(description: str | None = None) -> str:
    """Trigger a KB ingestion job. Returns the ingestionJobId."""
    settings = get_settings()
    settings.require("knowledge_base_id", "data_source_id")
    resp = bedrock_agent_client().start_ingestion_job(
        knowledgeBaseId=settings.knowledge_base_id,
        dataSourceId=settings.data_source_id,
        description=description or "RFP ingestion triggered by API/Project Service",
    )
    return resp["ingestionJob"]["ingestionJobId"]


def get_ingestion_status(ingestion_job_id: str) -> tuple[IngestionStatus, dict]:
    """Poll a specific ingestion job. Returns (mappedStatus, rawJob)."""
    settings = get_settings()
    settings.require("knowledge_base_id", "data_source_id")
    resp = bedrock_agent_client().get_ingestion_job(
        knowledgeBaseId=settings.knowledge_base_id,
        dataSourceId=settings.data_source_id,
        ingestionJobId=ingestion_job_id,
    )
    job = resp["ingestionJob"]
    raw = job.get("status", "IN_PROGRESS")
    return _STATUS_MAP.get(raw, IngestionStatus.INGESTING), job


def latest_ingestion_status() -> IngestionStatus:
    """Return the status of the most recent ingestion job, if any."""
    settings = get_settings()
    settings.require("knowledge_base_id", "data_source_id")
    resp = bedrock_agent_client().list_ingestion_jobs(
        knowledgeBaseId=settings.knowledge_base_id,
        dataSourceId=settings.data_source_id,
        sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        maxResults=1,
    )
    jobs = resp.get("ingestionJobSummaries", [])
    if not jobs:
        return IngestionStatus.QUEUED
    raw = jobs[0].get("status", "IN_PROGRESS")
    return _STATUS_MAP.get(raw, IngestionStatus.INGESTING)
