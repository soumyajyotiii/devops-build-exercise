"""Tool definitions for the underwriting agent.

These follow the Anthropic tool-use schema so the LLM can be wired up to
call them directly. The actual implementations are stubs; in production
each one writes to Postgres + S3 + SES (or files a task in our internal
queue).

The candidate's infrastructure is not expected to implement these tools.
They exist so the candidate can see how the agent is shaped and so the
service has a realistic surface area.
"""

from __future__ import annotations

import uuid
from typing import Any

# The tools as the LLM sees them (Anthropic tool-use format).
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "request_document_from_borrower",
        "description": (
            "Send a templated email to the borrower asking them to upload a "
            "specific document. Use when the underwriter note indicates a "
            "missing or insufficient document that the borrower can provide."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_type": {
                    "type": "string",
                    "description": "Specific document needed, e.g. 'February 2026 bank statement, all pages'.",
                },
                "rationale": {
                    "type": "string",
                    "description": "One-sentence explanation of why this document is needed.",
                },
                "example_acceptable": {
                    "type": "string",
                    "description": "Optional example of an acceptable submission to include in the email.",
                },
            },
            "required": ["document_type", "rationale"],
        },
    },
    {
        "name": "contact_appraiser",
        "description": (
            "Send a templated request to the appraiser of record. Use when "
            "the outstanding item requires action from the appraiser, not "
            "the borrower (e.g. legal description corrections)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "correction_needed": {"type": "string"},
                "supporting_reference": {"type": "string"},
            },
            "required": ["correction_needed"],
        },
    },
    {
        "name": "schedule_renewal_reminder",
        "description": (
            "Schedule a calendar-driven reminder for a renewal/expiry event "
            "(insurance, rate lock, etc). Use when no action is currently "
            "blocked, just a future deadline to surface."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_date": {
                    "type": "string",
                    "description": "ISO-8601 date for the reminder.",
                },
                "reason": {"type": "string"},
            },
            "required": ["reminder_date", "reason"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "File the item for human review when the agent's confidence is "
            "low or the situation is ambiguous (compound items, conflicting "
            "documents, borrower frustration)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "suggested_action": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
]


# ----- Stub implementations -----


def request_document_from_borrower(
    document_type: str,
    rationale: str,
    example_acceptable: str | None = None,
) -> dict[str, Any]:
    """Stub. In production: render template, write task record, hand off to SES."""
    return {
        "tool": "request_document_from_borrower",
        "task_id": f"task_{uuid.uuid4().hex[:12]}",
        "document_type": document_type,
        "rationale": rationale,
        "example_acceptable": example_acceptable,
        "status": "queued",
    }


def contact_appraiser(correction_needed: str, supporting_reference: str | None = None) -> dict[str, Any]:
    return {
        "tool": "contact_appraiser",
        "task_id": f"task_{uuid.uuid4().hex[:12]}",
        "correction_needed": correction_needed,
        "supporting_reference": supporting_reference,
        "status": "queued",
    }


def schedule_renewal_reminder(reminder_date: str, reason: str) -> dict[str, Any]:
    return {
        "tool": "schedule_renewal_reminder",
        "task_id": f"task_{uuid.uuid4().hex[:12]}",
        "reminder_date": reminder_date,
        "reason": reason,
        "status": "scheduled",
    }


def escalate_to_human(reason: str, suggested_action: str | None = None) -> dict[str, Any]:
    return {
        "tool": "escalate_to_human",
        "task_id": f"task_{uuid.uuid4().hex[:12]}",
        "reason": reason,
        "suggested_action": suggested_action,
        "status": "queued_for_review",
    }


TOOL_DISPATCH = {
    "request_document_from_borrower": request_document_from_borrower,
    "contact_appraiser": contact_appraiser,
    "schedule_renewal_reminder": schedule_renewal_reminder,
    "escalate_to_human": escalate_to_human,
}
