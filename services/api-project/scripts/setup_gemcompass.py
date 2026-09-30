"""Set up a new project for the GemCompass RFP + 3 BRD files, upload all,
ingest into the KB, and create a slide job. Drives the running api-project
service on :8000 exactly like a client would.

Run the api-project service first (uvicorn app.main:app --port 8000), then:
    python -m scripts.setup_gemcompass
"""
from __future__ import annotations

import pathlib
import sys
import time

import httpx

API = "http://127.0.0.1:8000/api/v1"
DOWNLOADS = pathlib.Path(r"C:\Users\user\Downloads")

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

# Main RFP first, then the three BRDs.
DOCS = [
    "GemCompass Platform RFP.docx",
    "Diamonds BRD v1.0.docx",
    "Jewelry BRD v1.0.docx",
    "Colored Stones BRD v1.0.docx",
]


def main() -> int:
    c = httpx.Client(timeout=120)

    # 1. Create the project.
    proj = c.post(f"{API}/projects", json={
        "name": "GemCompass Platform",
        "description": "GemCompass RFP + Diamonds/Jewelry/Colored Stones BRDs",
    }).json()
    project_id = proj["projectId"]
    print("projectId:", project_id)

    # 2. Upload each document (presign -> S3 PUT -> complete).
    document_ids: list[str] = []
    for name in DOCS:
        path = DOWNLOADS / name
        if not path.exists():
            print(f"  MISSING: {path}", file=sys.stderr)
            return 1
        data = path.read_bytes()
        presign = c.post(
            f"{API}/projects/{project_id}/documents/presign",
            json={"fileName": name, "contentType": DOCX_MIME, "sizeBytes": len(data)},
        ).json()
        doc_id = presign["documentId"]
        httpx.put(
            presign["uploadUrl"], content=data,
            headers={"Content-Type": DOCX_MIME}, timeout=120, follow_redirects=True,
        ).raise_for_status()
        c.post(
            f"{API}/projects/{project_id}/documents/complete",
            json={"documentId": doc_id, "objectKey": presign["objectKey"]},
        ).raise_for_status()
        document_ids.append(doc_id)
        print(f"  uploaded {name} -> {doc_id} ({len(data)//1024} KB)")

    # 3. Wait for KB ingestion to finish (larger DOCX set -> allow more time).
    print("waiting for KB ingestion (all 4 docs)...")
    for i in range(60):
        ks = c.get(f"{API}/projects/{project_id}/knowledge-status").json()
        print(f"  [{i}] kbStatus={ks['kbStatus']} ready={ks.get('readyCount')}/{ks.get('documentCount')}")
        if ks["kbStatus"] in ("READY", "FAILED"):
            break
        time.sleep(15)

    # 4. Create a slide job grounded on ALL documents.
    job = c.post(
        f"{API}/projects/{project_id}/slide-jobs",
        headers={"Idempotency-Key": "gemcompass-1"},
        json={
            "architectureType": "DEPLOYMENT",
            "instruction": (
                "Design a highly available, secure AWS deployment architecture "
                "for the GemCompass gem/jewelry grading and trading platform "
                "described in the RFP and the Diamonds, Jewelry, and Colored "
                "Stones BRDs."
            ),
            "documentIds": document_ids,
            "slideProfile": "AWS_16_9",
            "outputFormat": "PPTX",
        },
    ).json()
    print("jobId:", job["jobId"])

    print("\n--- Use these for the pipeline ---")
    print("PROJECT:", project_id)
    print("JOB    :", job["jobId"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
