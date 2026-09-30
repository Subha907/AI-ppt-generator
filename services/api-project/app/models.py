"""Pydantic domain models and request/response schemas.

These mirror the API and persistence contracts from the design document
(sections 7 and 9). Statuses follow the job status model in section 13.1.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Enums -----------------------------------------------------------------
class ProjectStatus(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    DELETING = "DELETING"


class IngestionStatus(str, Enum):
    QUEUED = "QUEUED"
    INGESTING = "INGESTING"
    READY = "READY"
    FAILED = "FAILED"


class ArchitectureType(str, Enum):
    BUSINESS = "BUSINESS"
    APPLICATION = "APPLICATION"
    DEPLOYMENT = "DEPLOYMENT"


class JobStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    ARCHITECTING = "ARCHITECTING"
    LAYOUT = "LAYOUT"
    COMPILING = "COMPILING"
    VISUAL_REVIEW = "VISUAL_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# --- API request bodies ----------------------------------------------------
class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    defaultThemeId: str = "aws-standard-v1"


class PresignRequest(BaseModel):
    fileName: str = Field(min_length=1, max_length=255)
    contentType: str
    sizeBytes: int = Field(gt=0)
    sha256: str | None = None


class CompleteUploadRequest(BaseModel):
    documentId: str
    objectKey: str
    etag: str | None = None


class StartJobRequest(BaseModel):
    architectureType: ArchitectureType
    instruction: str = Field(min_length=1, max_length=4000)
    documentIds: list[str] = Field(min_length=1)
    slideProfile: str = "AWS_16_9"
    outputFormat: str = "PPTX"


# --- API responses ---------------------------------------------------------
class ProjectResponse(BaseModel):
    projectId: str
    name: str
    description: str | None = None
    status: ProjectStatus
    kbStatus: IngestionStatus | None = None
    defaultThemeId: str
    createdAt: str
    updatedAt: str


class PresignResponse(BaseModel):
    documentId: str
    uploadUrl: str
    objectKey: str
    expiresInSeconds: int


class DocumentResponse(BaseModel):
    documentId: str
    fileName: str
    contentType: str
    sizeBytes: int
    s3Key: str
    ingestionStatus: IngestionStatus
    createdAt: str


class JobResponse(BaseModel):
    jobId: str
    projectId: str
    architectureType: ArchitectureType
    status: JobStatus
    currentState: str | None = None
    progressPercent: int = 0
    attempt: int = 0
    createdAt: str
    updatedAt: str


class KnowledgeStatusResponse(BaseModel):
    projectId: str
    kbStatus: IngestionStatus
    documentCount: int
    readyCount: int
    lastIngestionJobId: str | None = None
