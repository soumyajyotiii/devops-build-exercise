"""Request and response schemas for the underwriting agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ----- Request models (subset of the loan JSON the agent receives) -----


class Borrower(BaseModel):
    id: str
    name: str
    email: str
    entity: str | None = None
    communication_preferences: dict = Field(default_factory=dict)


class OutstandingItem(BaseModel):
    id: str
    category: str
    priority: Literal["low", "medium", "high"]
    status: str
    source: str
    underwriter_notes: str
    documents_on_file: list[dict] = Field(default_factory=list)
    previous_attempts: list[dict] = Field(default_factory=list)


class LoanContext(BaseModel):
    """The slice of loan state the agent needs to act on a single item."""

    loan_id: str
    loan_type: str
    borrower: Borrower
    item: OutstandingItem
    timeline_pressure: dict = Field(default_factory=dict)


# ----- Response models -----


class AgentAction(BaseModel):
    """A structured action the agent decided to take."""

    action_type: Literal[
        "request_document_from_borrower",
        "contact_appraiser",
        "schedule_renewal_reminder",
        "escalate_to_human",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    draft_email: str | None = None
    routing_target: str | None = None  # email address or human queue id


class AgentResult(BaseModel):
    """Full response returned by POST /v1/items/process."""

    loan_id: str
    item_id: str
    action: AgentAction
    llm_model: str
    latency_ms: int
    audit_id: str  # opaque id pointing to the durable audit log entry
    completed_at: datetime
