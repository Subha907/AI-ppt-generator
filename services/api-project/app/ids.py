"""ID generation helpers.

We use ULIDs (sortable, timestamp-prefixed) rendered with human-readable
prefixes so IDs are self-describing in logs and DynamoDB keys, e.g.
``prj_01J...``, ``doc_01J...``, ``job_01J...``, ``art_01J...``.
"""
from __future__ import annotations

from ulid import ULID


def _new(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def new_project_id() -> str:
    return _new("prj")


def new_document_id() -> str:
    return _new("doc")


def new_job_id() -> str:
    return _new("job")


def new_artifact_id() -> str:
    return _new("art")
