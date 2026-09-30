# API and Project Service

Component 1 of the Multi-Agent AWS Architecture Slide Generator.

This service runs **locally** but uses **real AWS services** in `us-west-2`,
authenticated through your **AWS SSO** profile. It hosts **no AI agent** - it is
the setup-phase platform service that prepares the project + ingested RFPs that
the four-agent pipeline later consumes.

## What it does

- Create/list/get projects (DynamoDB, tenant-scoped)
- Presigned direct-to-S3 RFP uploads (PDF/DOCX)
- Trigger and poll Bedrock Knowledge Base ingestion (embeddings stored in S3 Vectors)
- Record slide-job requests (idempotent) for the orchestrator to pick up later

## AWS resources (provisioned by CloudFormation)

| Resource | Name | Purpose |
|----------|------|---------|
| S3 data bucket | `slidegen-data-<acct>-us-west-2` | RFP source files + artifacts |
| S3 Vectors bucket + index | `slidegen-vectors-<acct>` / `slidegen-kb-index` | embedding storage |
| DynamoDB table | `ArchitectureGenerator` | projects/docs/jobs/artifacts/idempotency |
| Bedrock Knowledge Base | `slidegen-kb` | chunk + embed (Titan v2) RFPs into S3 Vectors |
| IAM role | `slidegen-kb-role` | assumed by the KB |

The infrastructure is split across two stacks (`infrastructure/cloudformation/`):
`01-base-stack.yaml` (buckets, index, table, role) and `02-kb-stack.yaml` (KB +
data source). The KB is separate because Bedrock KB early-validation requires the
S3 Vectors index to already exist before the KB can reference it.

## Prerequisites

- Python 3.11+
- An AWS SSO profile in `~/.aws/config` (this project uses `sdg`)
- Bedrock model access enabled for **Titan Text Embeddings v2** in `us-west-2`

## Setup

```powershell
# 1. Create the virtualenv and install deps
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Configure. Copy the example and set your SSO profile/region.
Copy-Item .env.example .env
#    (AWS_PROFILE=sdg, AWS_REGION=us-west-2 already set)

# 3. Log in to AWS SSO (opens a browser)
aws sso login --profile sdg
```

## Deploy infrastructure

Deploys both CloudFormation stacks and writes the resource names into `.env`:

```powershell
.\.venv\Scripts\python.exe -m scripts.deploy_stack --write-env
```

> Uses boto3 (not the AWS CLI) so it works even with an older CLI that predates
> Bedrock/S3 Vectors.

## Run the service locally

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

- Health + SSO identity check: http://127.0.0.1:8000/healthz
- Interactive API docs: http://127.0.0.1:8000/docs

## Smoke test (end-to-end against AWS)

With the service running in another terminal:

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_test
```

This creates a project, uploads a sample PDF to S3, triggers KB ingestion, and
polls until the knowledge base reports `READY`.

## SSO notes

- The app authenticates through `AWS_PROFILE` (SSO). All AWS calls use the
  short-lived credentials from `aws sso login`.
- When the token expires, `/healthz` returns 503 with a message telling you to
  run `aws sso login --profile sdg` again. No code/config change needed.

## API endpoints (base path `/api/v1`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/projects` | Create project |
| GET | `/projects` | List projects |
| GET | `/projects/{id}` | Get project |
| POST | `/projects/{id}/documents/presign` | Presigned upload URL |
| POST | `/projects/{id}/documents/complete` | Register upload + start ingestion |
| GET | `/projects/{id}/documents` | List documents |
| GET | `/projects/{id}/knowledge-status` | Ingestion readiness |
| POST | `/projects/{id}/slide-jobs` | Start slide job (Idempotency-Key header) |
| GET | `/projects/{id}/slide-jobs` | List jobs |
| GET | `/projects/{id}/slide-jobs/{jobId}` | Get job |
