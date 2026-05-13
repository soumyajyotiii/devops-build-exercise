# underwriting-agent

Reference skeleton for the **Underwriting-Assist Agent** — the LLM-powered
service that processes outstanding underwriting items on approved-pending
loans at Saaf Finance.

> This repo is **deliberately minimal**. The agent logic is a stub. Your job
> for the take-home is **not** to extend it. Your job is to design and build
> the infrastructure that runs it in production. See
> `service_spec.md` (handed over separately) for the runtime requirements.

## What's in here

```
underwriting-agent/
├── pyproject.toml          # Python deps
├── Dockerfile              # Multi-stage container build
├── .env.example            # Required runtime environment variables
├── .gitignore
├── src/agent/
│   ├── main.py             # FastAPI app: POST /v1/items/process
│   ├── schema.py           # Pydantic request/response models
│   ├── tools.py            # Tool definitions (Anthropic tool-use format)
│   ├── llm.py              # Thin Anthropic / Bedrock client wrapper
│   ├── store.py            # Postgres + S3 data access
│   └── config.py           # Settings from environment variables
├── tests/
│   └── test_smoke.py       # One trivial pytest
└── examples/
    └── sample_loan.json    # One mock loan payload for local testing
```

## Run locally

```bash
# 1. Copy env vars
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY for live runs.
# For local development the LLM call is mocked when ANTHROPIC_API_KEY is unset.

# 2. Install deps (Python 3.11+ required)
pip install -e ".[dev]"

# 3. Run the service
uvicorn agent.main:app --host 0.0.0.0 --port 8080 --reload

# 4. Smoke test
curl -X POST http://localhost:8080/v1/items/process \
  -H "Content-Type: application/json" \
  -d @examples/sample_loan.json | jq
```

Or via Docker:

```bash
docker build -t underwriting-agent:dev .
docker run --rm -p 8080:8080 --env-file .env underwriting-agent:dev
```

## Health checks

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` | Liveness — returns 200 if the process is up |
| `GET /readyz`  | Readiness — checks Postgres + S3 connectivity |

## Required environment variables

See `.env.example` for the full list. At minimum the service needs:

- `DATABASE_URL` — Postgres connection string
- `S3_BUCKET` — Bucket holding uploaded borrower documents
- `ANTHROPIC_API_KEY` — LLM provider key (or use AWS Bedrock; see `config.py`)
- `AWS_REGION` — Region for SES + S3
- `SES_FROM_ADDRESS` — Verified sender for outbound borrower email
