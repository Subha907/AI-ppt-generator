"""Create a test fixture: project + ingested sample RFP + a slide job.

Drives the running api-project service (port 8000) exactly like a client would,
then prints the projectId and jobId to feed into Agent 1.
"""
from __future__ import annotations

import pathlib
import sys
import time

import httpx

API = "http://127.0.0.1:8000/api/v1"
SAMPLE_RFP = (
    pathlib.Path(__file__).resolve().parents[2]
    / "api-project"
    / "sample_rfp.pdf"
)


def main() -> int:
    if not SAMPLE_RFP.exists():
        print(f"Sample RFP not found at {SAMPLE_RFP}", file=sys.stderr)
        return 1
    pdf = SAMPLE_RFP.read_bytes()
    c = httpx.Client(timeout=60)

    proj = c.post(f"{API}/projects", json={"name": "Agent1 Test", "description": "fixture"}).json()
    project_id = proj["projectId"]
    print("projectId:", project_id)

    presign = c.post(
        f"{API}/projects/{project_id}/documents/presign",
        json={"fileName": "sample_rfp.pdf", "contentType": "application/pdf", "sizeBytes": len(pdf)},
    ).json()
    document_id = presign["documentId"]
    print("documentId:", document_id)

    httpx.put(presign["uploadUrl"], content=pdf, headers={"Content-Type": "application/pdf"}, timeout=60, follow_redirects=True).raise_for_status()

    c.post(
        f"{API}/projects/{project_id}/documents/complete",
        json={"documentId": document_id, "objectKey": presign["objectKey"]},
    ).raise_for_status()

    print("waiting for KB ingestion...")
    for _ in range(24):
        ks = c.get(f"{API}/projects/{project_id}/knowledge-status").json()
        print("  kbStatus:", ks["kbStatus"])
        if ks["kbStatus"] in ("READY", "FAILED"):
            break
        time.sleep(10)

    job = c.post(
        f"{API}/projects/{project_id}/slide-jobs",
        headers={"Idempotency-Key": "agent1-fixture-1"},
        json={
            "architectureType": "DEPLOYMENT",
            "instruction": "Design a highly available, secure AWS deployment architecture for the RFP.",
            "documentIds": [document_id],
            "slideProfile": "AWS_16_9",
            "outputFormat": "PPTX",
        },
    ).json()
    print("jobId:", job["jobId"])

    print("\n--- Run Agent 1 with: ---")
    print(f'  --project {project_id} --job {job["jobId"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
