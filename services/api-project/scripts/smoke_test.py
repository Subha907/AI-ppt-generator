"""End-to-end smoke test against real AWS (uses the running local service).

Flow:
  1. GET /healthz                       -> confirms SSO auth
  2. POST /projects                     -> create a project (DynamoDB)
  3. POST .../documents/presign         -> presigned S3 PUT URL
  4. PUT <presigned url>                -> upload a real sample PDF to S3
  5. POST .../documents/complete        -> head object + trigger KB ingestion
  6. GET .../knowledge-status           -> poll ingestion state
  7. GET .../documents                  -> list documents

Run the service first:
    uvicorn app.main:app --port 8000
Then:
    python -m scripts.smoke_test
"""
from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"


import pathlib

SAMPLE_RFP = pathlib.Path(__file__).resolve().parents[1] / "sample_rfp.pdf"


def _rfp_bytes() -> bytes:
    """Use the realistic sample RFP if present; otherwise a tiny fallback PDF."""
    if SAMPLE_RFP.exists():
        return SAMPLE_RFP.read_bytes()
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length 74>>stream\n"
        b"BT /F1 18 Tf 72 700 Td (RFP: build a highly available AWS deployment.) Tj ET\n"
        b"endstream endobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"trailer<</Root 1 0 R/Size 6>>\nstartxref\n0\n%%EOF"
    )


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=60)

    print("1) /healthz")
    r = c.get("/healthz")
    r.raise_for_status()
    print("   ", r.json()["aws"]["arn"])

    print("2) create project")
    r = c.post(
        "/api/v1/projects",
        json={"name": "Smoke Test Project", "description": "e2e smoke"},
    )
    r.raise_for_status()
    project_id = r.json()["projectId"]
    print("   projectId:", project_id)

    print("3) presign upload")
    pdf = _rfp_bytes()
    print(f"   using RFP of {len(pdf)} bytes")
    r = c.post(
        f"/api/v1/projects/{project_id}/documents/presign",
        json={
            "fileName": "rfp.pdf",
            "contentType": "application/pdf",
            "sizeBytes": len(pdf),
        },
    )
    r.raise_for_status()
    presign = r.json()
    document_id = presign["documentId"]
    print("   documentId:", document_id)

    print("4) PUT to presigned S3 URL")
    put = httpx.put(
        presign["uploadUrl"],
        content=pdf,
        headers={"Content-Type": "application/pdf"},
        timeout=60,
        follow_redirects=True,
    )
    put.raise_for_status()
    print("   S3 PUT status:", put.status_code)

    print("5) complete upload (triggers KB ingestion)")
    r = c.post(
        f"/api/v1/projects/{project_id}/documents/complete",
        json={"documentId": document_id, "objectKey": presign["objectKey"]},
    )
    r.raise_for_status()
    print("   ", r.json())

    print("6) poll knowledge-status (up to ~3 min)")
    for i in range(18):
        r = c.get(f"/api/v1/projects/{project_id}/knowledge-status")
        r.raise_for_status()
        status = r.json()["kbStatus"]
        print(f"   [{i}] kbStatus={status}")
        if status in ("READY", "FAILED"):
            break
        time.sleep(10)

    print("7) list documents")
    r = c.get(f"/api/v1/projects/{project_id}/documents")
    r.raise_for_status()
    for d in r.json():
        print("   ", d["documentId"], d["ingestionStatus"])

    print("\nSMOKE TEST DONE. projectId =", project_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
