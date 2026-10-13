# Multi-Agent AWS Architecture Slide Generator

Turn an uploaded RFP into an editable, AWS-styled architecture PowerPoint slide.
The system is a pipeline of four cooperating agents. Each agent does one job,
hands a validated, contract-shaped artifact to the next, and all artifacts are
persisted to S3 and tracked in DynamoDB. Generation is grounded in a Bedrock
Knowledge Base built from the project's own RFP documents.

## What it does, end to end

1. A user creates a project and uploads one or more RFP documents.
2. The RFPs are ingested into a Bedrock Knowledge Base (embeddings in S3 Vectors).
3. The user starts a slide job for a given diagram type (e.g. `deployment.multi_az`).
4. Four agents run in sequence to produce a finished `.pptx`:
   - **Agent 1 - Solution Architect** builds the logical architecture topology.
   - **Agent 2 - Layout & Geometry** places that topology on a 16:9 slide.
   - **Agent 3 - Compiler & Renderer** compiles an editable PPTX and a preview image.
   - **Agent 4 - Visual Reviewer** inspects the preview and returns PASS or REVISE.
5. On REVISE, the orchestrator loops back to Agents 2/3 to apply corrections;
   on PASS, the artifact is published.

## Pipeline / data flow

```
RFP upload ──► Knowledge Base (Bedrock + S3 Vectors)
                     │  evidence (grounding)
                     ▼
  Agent 1  ──►  architecture-model.json   (topology: nodes + edges, no coordinates)
  Agent 2  ──►  layout-model.json         (absolute geometry on a 16:9 slide)
  Agent 3  ──►  architecture.pptx + slide preview.png
  Agent 4  ──►  review report (PASS / REVISE + issues)
                     │
                     └─ REVISE ─► back to Agent 2/3   │   PASS ─► publish
```

Each stage reads the previous stage's artifact from S3, validates its own output
against a JSON contract, persists it, and advances the job's status in DynamoDB.

Artifacts are laid out per job in the data bucket:

```
.../projects/{projectId}/jobs/{jobId}/
  models/architecture-model.json      (Agent 1)
  models/layout-model.json            (Agent 2)
  outputs/version-1/architecture.pptx (Agent 3)
  previews/attempt-1/slide-1.png      (Agent 3 preview)
  reviews/attempt-1.json              (Agent 4)
```

## The agents

### Agent 1 - Solution Architect (`services/architecture-intelligence`, port 8001)
**Purpose:** produce a *logical* architecture topology grounded in the RFP.

- **Flow:** `retrieve -> generate -> validate -> persist` (`app/agent.py`).
- Retrieves evidence chunks from the Knowledge Base, scoped strictly to the
  caller's tenant, project, and selected documents (`app/retrieval.py`), so a
  job can only ever see its own RFP content.
- Generates the model via Amazon Nova Pro (Bedrock Converse) using a diagram-type
  specific system prompt, or a deterministic `stub` provider for offline runs
  (`app/generation.py`, `app/prompts.py`).
- Output: `nodes` + `edges` only, **no coordinates** - placement is Agent 2's
  job. Shaped by `packages/contracts/architecture_model.contract.json`.
- Supports many diagram types (e.g. `deployment.multi_az`, `deployment.multi_region`,
  `application.microservices`, `genai.rag`), each backed by a semantic rule-pack
  under `services/architecture-intelligence/rules/semantic/`.

### Agent 2 - Layout & Geometry (`services/layout`, port 8002)
**Purpose:** turn the abstract topology into concrete slide geometry.

- **Flow:** `load architecture model -> compute layout -> validate -> persist`
  (`app/agent.py`).
- Places every node with absolute coordinates (inches) on a 16:9 slide, assigns
  z-order, nests containers (VPC / AZ / subnet), and routes connectors.
- Pluggable layout engines with graceful fallback: `stack` / `stack-abs`
  (AWS-style swimlane stacking), `graphviz` (dot placement + routing), and a
  hand-rolled `grid` engine.
- Validates geometry gates - **overlap count and out-of-bounds count must be 0**.
- Output shaped by `packages/contracts/layout_model.contract.json`.

### Agent 3 - Compiler & Renderer (`services/render-review`, port 8003)
**Purpose:** compile the layout into a real, editable PowerPoint and a preview.

- **Flow:** `load layout -> compile PPTX -> validate -> render preview -> persist`
  (`app/agent.py`, `app/compiler.py`, `app/preview.py`).
- Emits **native, editable PPTX shapes** (not a flat image), resolving icon keys
  to AWS service icons.
- Validates the compiled package against the quality gates, then renders a PNG
  preview that Agent 4 reviews.

### Agent 4 - Visual Reviewer (`services/render-review`, port 8003)
**Purpose:** judge the rendered slide and decide publish vs. revise.

- **Flow:** `load preview + layout -> VLM review -> validate -> persist`
  (`app/review_agent.py`).
- Uses a vision model (or stub) to inspect the preview image against visual
  quality rules (overlap, clipped text, low contrast, excessive connector
  crossings, inconsistent spacing, unreadable labels, out of bounds).
- Returns a `decision` (`PASS` / `REVISE`), a `score` (0-100), and a list of
  `issues` with recommended corrective actions (resize, move, respace, reroute,
  etc.). It only **reports**; the orchestrator applies fixes by re-running
  Agents 2/3. Output shaped by `packages/contracts/review_model.contract.json`.

### Supporting service: API & Project Service (`services/api-project`, port 8000)
**Not an agent.** It runs the setup phase: project CRUD, presigned RFP uploads,
upload completion plus Knowledge Base ingestion, knowledge-readiness status, and
creating the slide-job record the pipeline consumes (`app/main.py`). It only
*creates* the job; the four agents are invoked separately in sequence.

## Contracts (`packages/contracts/`)

The contracts are the hand-off boundary between agents - each agent validates
its output against the next consumer's expectations:

- **`architecture_model.contract.json`** - Agent 1 output (logical nodes/edges).
- **`layout_model.contract.json`** - Agent 2 output (absolute slide geometry).
- **`review_model.contract.json`** - Agent 4 output (PASS/REVISE + issues),
  which drives the correction loop.

## Infrastructure (`infrastructure/cloudformation/`)

- **`01-base-stack.yaml`** - private data bucket (RFPs + artifacts), a shared
  read-only reference bucket (AWS icons/themes), the S3 Vectors bucket + index
  for embeddings, the single DynamoDB table, and the IAM role the Bedrock
  Knowledge Base assumes. Deploy this first.
- **`02-kb-stack.yaml`** - the Bedrock Knowledge Base (deployed second, because
  it must reference an existing S3 Vectors index).

AWS services used: **Bedrock** (Nova Pro for generation, Titan v2 for 1024-dim
embeddings, Knowledge Base retrieval), **S3** + **S3 Vectors**, **DynamoDB**,
and **IAM**. Target region: `us-west-2`.

## Running locally

Each service is a FastAPI app run with uvicorn. Start all four, then drive the
pipeline by calling each agent's internal endpoint in order:

| Port | Service | Role |
|------|---------|------|
| 8000 | api-project | project setup + job creation (no agent) |
| 8001 | architecture-intelligence | Agent 1 |
| 8002 | layout | Agent 2 |
| 8003 | render-review | Agent 3 (compile) + Agent 4 (review) |

A valid AWS session is required for the real (`nova`) providers; use the `stub`
providers to exercise the full pipeline without model access. See `steps.md` for
a complete, copy-paste runbook (endpoints, request bodies, and a PowerShell
runner) for re-running a job end to end.
