"""Drive Agents 1 -> 2 -> 3 through their HTTP APIs and print the artifacts.

Usage:
    python -m scripts.run_pipeline_http --project <projectId> --job <jobId> --version 2
"""
from __future__ import annotations

import argparse
import json

import httpx

A1 = "http://127.0.0.1:8001/internal/v1/architectures/generate"
A2 = "http://127.0.0.1:8002/internal/v1/layouts/generate"
A3 = "http://127.0.0.1:8003/internal/v1/presentations/compile"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--version", type=int, default=2)
    args = p.parse_args()

    c = httpx.Client(timeout=240)

    print("==== AGENT 1: Solution Architect (Nova Pro) ====")
    r1 = c.post(A1, json={"projectId": args.project, "jobId": args.job, "provider": "nova"})
    r1.raise_for_status()
    d1 = r1.json()
    print(json.dumps(d1, indent=2))

    print("\n==== AGENT 2: Layout & Geometric (deterministic) ====")
    r2 = c.post(A2, json={"projectId": args.project, "jobId": args.job})
    r2.raise_for_status()
    d2 = r2.json()
    print(json.dumps(d2, indent=2))

    print("\n==== AGENT 3: Compiler (editable PPTX + preview) ====")
    r3 = c.post(A3, json={
        "projectId": args.project,
        "jobId": args.job,
        "version": args.version,
        "attempt": args.version,
    })
    r3.raise_for_status()
    d3 = r3.json()
    print(json.dumps(d3, indent=2))

    print("\n==== ARTIFACTS ====")
    print("architecture-model.json:", d1["model_s3_uri"])
    print("layout-model.json      :", d2["layout_s3_uri"])
    print("architecture.pptx      :", d3["pptx_s3_uri"])
    print("preview slide-1.png    :", d3["preview_s3_uri"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
