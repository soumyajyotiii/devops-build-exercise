"""FastAPI entrypoint.

Exposes:
- POST /v1/items/process — run the agent on a single outstanding item
- GET  /healthz          — liveness probe
- GET  /readyz           — readiness probe (checks Postgres + S3)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .llm import LLMClient
from .schema import AgentResult, LoanContext
from .store import Store

logging.basicConfig(
    level=get_settings().log_level,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("agent")

app = FastAPI(
    title="Saaf underwriting-assist agent",
    version="0.1.0",
    description="Reference skeleton — handed to candidates for the DevOps take-home.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Singletons. The infrastructure design decides how these are scaled
# (e.g. one process per Fargate task, one worker per SQS consumer).
_llm = LLMClient()
_store = Store()


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
def readyz(response: Response) -> dict[str, object]:
    health = _store.health()
    if not health.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "postgres": health.postgres,
        "s3": health.s3,
        "ready": health.ok,
    }


@app.post("/v1/items/process", response_model=AgentResult, tags=["agent"])
def process_item(ctx: LoanContext) -> AgentResult:
    """Run the agent on one outstanding underwriting item."""
    logger.info(
        "process_item.start",
        extra={"loan_id": ctx.loan_id, "item_id": ctx.item.id, "category": ctx.item.category},
    )

    try:
        action, latency_ms, model_used = _llm.classify_and_decide(ctx)
    except Exception as exc:  # noqa: BLE001
        logger.exception("llm.failure", extra={"loan_id": ctx.loan_id, "item_id": ctx.item.id})
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}") from exc

    audit_id = _store.write_audit_record(
        {
            "loan_id": ctx.loan_id,
            "item_id": ctx.item.id,
            "model": model_used,
            "input": ctx.model_dump(),
            "output": action.model_dump(),
            "latency_ms": latency_ms,
        }
    )

    return AgentResult(
        loan_id=ctx.loan_id,
        item_id=ctx.item.id,
        action=action,
        llm_model=model_used,
        latency_ms=latency_ms,
        audit_id=audit_id,
        completed_at=datetime.now(timezone.utc),
    )
