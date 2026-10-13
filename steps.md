# Re-running a Deployment Diagram Job

How to re-run the 4-agent pipeline on an **existing project** to generate an AWS
deployment (multi-AZ) architecture diagram.

The `api-project` service (port 8000) only **creates the job record**. The
pipeline itself is orchestrated by calling each agent's internal endpoint in
sequence: Agent 1 (8001) -> Agent 2 (8002) -> Agent 3 (8003) -> Agent 4 (8003).

## Prerequisites

- All four services running:
  - `8000` - API & Project service
  - `8001` - Agent 1 (Architecture Intelligence)
  - `8002` - Agent 2 (Layout)
  - `8003` - Agent 3 (Compile) + Agent 4 (Review)
- Valid AWS SSO session: `aws sso login --profile sdg`
  - The token expires hourly. boto caches it at startup, so **restart the
    services after re-logging in**.
- An existing `projectId` and an ingested `documentId`, e.g.:
  - `projectId = prj_01M3GZ2D2EJ8Y60MXKY6QNV5P0`
  - `documentId = doc_01M3GZ2G4P51G38TN8YKJTZ3X4`

## API Steps

### Step 1 - Create the job (api-project, 8000)

`POST http://127.0.0.1:8000/api/v1/projects/{projectId}/slide-jobs`

```json
{
  "category": "deployment",
  "subCategory": "multi_az",
  "instruction": "Design a production multi-AZ deployment architecture for a 3-tier web application on AWS with high availability.",
  "documentIds": ["doc_01M3GZ2G4P51G38TN8YKJTZ3X4"]
}
```

Returns a new `jobId` (status `QUEUED`). Capture it for the next steps.

Payload fields:
- `category` + `subCategory` (preferred) **or** legacy `architectureType`
  (e.g. `deployment.multi_az`). At least one of `category` / `architectureType`
  is required.
- `instruction` - required, 1-4000 chars.
- `documentIds` - required, at least one.
- `slideProfile` - optional, default `AWS_16_9`.
- `outputFormat` - optional, default `PPTX`.

> Pass an `Idempotency-Key` header to get the same job back on a repeated call
> instead of minting a new one.

### Step 2 - Agent 1: generate architecture model (8001)

`POST http://127.0.0.1:8001/internal/v1/architectures/generate`

```json
{ "projectId": "prj_01M3GZ2D2EJ8Y60MXKY6QNV5P0", "jobId": "<jobId>", "provider": "nova" }
```

`provider`: `nova` (real Nova model) or `stub`.

### Step 3 - Agent 2: generate layout (8002)

`POST http://127.0.0.1:8002/internal/v1/layouts/generate`

```json
{ "projectId": "prj_01M3GZ2D2EJ8Y60MXKY6QNV5P0", "jobId": "<jobId>", "engine": "stack-abs" }
```

`engine`: use `stack-abs` (absolute-swimlane engine with the deployment fixes).
The response includes `overlap_count` and `out_of_bounds_count` - both must be `0`.

### Step 4 - Agent 3: compile PPTX + preview (8003)

`POST http://127.0.0.1:8003/internal/v1/presentations/compile`

```json
{ "projectId": "prj_01M3GZ2D2EJ8Y60MXKY6QNV5P0", "jobId": "<jobId>", "version": 1, "attempt": 1 }
```

Returns `pptx_s3_uri` and `preview_s3_uri`.

### Step 5 - Agent 4: review (8003)

`POST http://127.0.0.1:8003/internal/v1/presentations/review`

```json
{ "projectId": "prj_01M3GZ2D2EJ8Y60MXKY6QNV5P0", "jobId": "<jobId>", "attempt": 1, "provider": "nova" }
```

Returns `decision` (`PASS` / `FAIL`), `score`, and `issues[]`.

## Copy-paste PowerShell runner

PowerShell mangles inline JSON, so write each body to a file and use
`--data-binary @file -o out`.

```powershell
$proj = "prj_01M3GZ2D2EJ8Y60MXKY6QNV5P0"
$doc  = "doc_01M3GZ2G4P51G38TN8YKJTZ3X4"

# 1. create job
'{"category":"deployment","subCategory":"multi_az","instruction":"Design a production multi-AZ deployment for a 3-tier web app with HA.","documentIds":["'+$doc+'"]}' | Set-Content _job.json
curl.exe -s -X POST "http://127.0.0.1:8000/api/v1/projects/$proj/slide-jobs" -H "Content-Type: application/json" --data-binary "@_job.json" -o _jobresp.json
$job = (Get-Content _jobresp.json | ConvertFrom-Json).jobId
Write-Output "jobId=$job"

# 2. Agent 1 - architecture
'{"projectId":"'+$proj+'","jobId":"'+$job+'","provider":"nova"}' | Set-Content _gen.json
curl.exe -s -X POST "http://127.0.0.1:8001/internal/v1/architectures/generate" -H "Content-Type: application/json" --data-binary "@_gen.json" -o _genresp.json

# 3. Agent 2 - layout
'{"projectId":"'+$proj+'","jobId":"'+$job+'","engine":"stack-abs"}' | Set-Content _lay.json
curl.exe -s -X POST "http://127.0.0.1:8002/internal/v1/layouts/generate" -H "Content-Type: application/json" --data-binary "@_lay.json" -o _layresp.json

# 4. Agent 3 - compile
'{"projectId":"'+$proj+'","jobId":"'+$job+'","version":1,"attempt":1}' | Set-Content _cmp.json
curl.exe -s -X POST "http://127.0.0.1:8003/internal/v1/presentations/compile" -H "Content-Type: application/json" --data-binary "@_cmp.json" -o _cmpresp.json

# 5. Agent 4 - review
'{"projectId":"'+$proj+'","jobId":"'+$job+'","attempt":1,"provider":"nova"}' | Set-Content _rev.json
curl.exe -s -X POST "http://127.0.0.1:8003/internal/v1/presentations/review" -H "Content-Type: application/json" --data-binary "@_rev.json" -o _revresp.json
Get-Content _revresp.json
```

## Output locations (S3)

```
s3://slidegen-data-363811234194-us-west-2/artifacts/tenant-local-dev/projects/{projectId}/jobs/{jobId}/
  models/architecture-model.json      (Agent 1)
  models/layout-model.json            (Agent 2)
  outputs/version-1/architecture.pptx (Agent 3)
  previews/attempt-1/slide-1.png      (Agent 3 preview)
  reviews/attempt-1.json              (Agent 4)
```

## Notes

- **Fresh vs. existing job**: Step 1 always mints a new `jobId`. To re-run a
  *specific existing* job's later stages (e.g. just re-compile after a code
  change), skip Step 1 and reuse that job's ID in steps 2-5. The architecture
  model is cached in S3, so you can even skip Agent 1 and start at Agent 2.
- **Different diagram type**: change `subCategory` in Step 1 (e.g.
  `network_focused`, `multi_region`). The rest of the flow is identical.
- **Poll job status** any time:
  `GET http://127.0.0.1:8000/api/v1/projects/{projectId}/slide-jobs/{jobId}`
- **After editing agent code**, restart that service (uvicorn runs without
  reload) before re-running the affected stage.
