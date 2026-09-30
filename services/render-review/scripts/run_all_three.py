"""Create BUSINESS + APPLICATION jobs on an existing project and run the full
pipeline (Agent 1->2->3->4) for all three architecture types, so one project
yields Business, Application, and Deployment diagrams.

Assumes the project already has ingested documents. Uses the running services
(api-project 8000, agents 8001/8002/8003).

    python -m scripts.run_all_three --project <projectId> --documents doc1,doc2,...
"""
from __future__ import annotations

import argparse
import json

import httpx

APIP = "http://127.0.0.1:8000/api/v1"
A1 = "http://127.0.0.1:8001/internal/v1/architectures/generate"
A2 = "http://127.0.0.1:8002/internal/v1/layouts/generate"
A3 = "http://127.0.0.1:8003/internal/v1/presentations/compile"
A4 = "http://127.0.0.1:8003/internal/v1/presentations/review"

INSTRUCTIONS = {
    "BUSINESS": ("Create a business architecture showing the business "
                 "capabilities, actors, and processes for the platform."),
    "APPLICATION": ("Create an application architecture showing the application "
                    "components, services, and their interactions."),
    "DEPLOYMENT": ("Create a highly available, secure AWS deployment "
                   "architecture for the platform."),
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--documents", required=True, help="comma-separated documentIds")
    p.add_argument("--types", default="BUSINESS,APPLICATION,DEPLOYMENT")
    args = p.parse_args()

    doc_ids = [d.strip() for d in args.documents.split(",") if d.strip()]
    c = httpx.Client(timeout=300)

    for arch_type in [t.strip() for t in args.types.split(",")]:
        print(f"\n############## {arch_type} ##############")
        # 1. Create a job for this architecture type.
        job = c.post(
            f"{APIP}/projects/{args.project}/slide-jobs",
            headers={"Idempotency-Key": f"allthree-{arch_type}"},
            json={
                "architectureType": arch_type,
                "instruction": INSTRUCTIONS[arch_type],
                "documentIds": doc_ids,
                "slideProfile": "AWS_16_9",
                "outputFormat": "PPTX",
            },
        ).json()
        job_id = job["jobId"]
        print("jobId:", job_id)

        # 2. Agent 1 -> 2 -> 3 -> 4
        r1 = c.post(A1, json={"projectId": args.project, "jobId": job_id, "provider": "nova"})
        r1.raise_for_status()
        print("  Agent1:", {k: r1.json()[k] for k in ("node_count", "edge_count", "evidence_count")})

        r2 = c.post(A2, json={"projectId": args.project, "jobId": job_id})
        r2.raise_for_status()
        print("  Agent2:", {k: r2.json()[k] for k in ("engine", "object_count", "overlap_count")})

        r3 = c.post(A3, json={"projectId": args.project, "jobId": job_id, "version": 1, "attempt": 1})
        r3.raise_for_status()
        print("  Agent3:", r3.json()["stats"])
        print("  pptx:", r3.json()["pptx_s3_uri"])

        r4 = c.post(A4, json={"projectId": args.project, "jobId": job_id, "attempt": 1, "provider": "nova"})
        r4.raise_for_status()
        d4 = r4.json()
        print(f"  Agent4: decision={d4['decision']} score={d4['score']} issues={d4['issue_count']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
