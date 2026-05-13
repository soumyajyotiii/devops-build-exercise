"""Thin LLM client wrapper.

Wraps Anthropic + Bedrock behind a single ``classify_and_decide`` call so
the rest of the service doesn't depend on a specific provider SDK.

The candidate is not expected to touch this file. The infrastructure they
build only needs to ensure the runtime has network egress to the configured
LLM provider, the correct credentials are mounted, and outbound traffic is
audited.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .config import Settings, get_settings
from .schema import AgentAction, LoanContext

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are Saaf Finance's underwriting-assist agent.
You receive a single outstanding underwriting item on a DSCR investment
mortgage loan. You decide which tool to call to move the item forward.

Be specific in your rationale. If the situation is ambiguous, escalate.
"""


class LLMClient:
    """Provider-agnostic LLM client."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._anthropic = None

        if self.settings.llm_provider == "anthropic" and self.settings.anthropic_api_key:
            try:
                from anthropic import Anthropic
                self._anthropic = Anthropic(api_key=self.settings.anthropic_api_key)
            except ImportError:  # pragma: no cover
                logger.warning("anthropic SDK not installed; falling back to mock")

    def classify_and_decide(self, ctx: LoanContext) -> tuple[AgentAction, int, str]:
        """Run the LLM. Returns (action, latency_ms, model_used)."""
        start = time.perf_counter()

        if self.settings.llm_is_mocked or self._anthropic is None:
            action = self._mock_action(ctx)
            return action, int((time.perf_counter() - start) * 1000), "mock"

        # Real Anthropic call would go here. The actual tool-use dance is
        # deliberately kept simple — the take-home is about deploying this,
        # not improving it.
        from .tools import TOOL_DEFINITIONS

        message = self._anthropic.messages.create(
            model=self.settings.llm_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,  # type: ignore[arg-type]
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(ctx.model_dump(), default=str),
                }
            ],
        )

        action = self._action_from_response(message)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return action, latency_ms, self.settings.llm_model

    # ----- helpers -----

    def _mock_action(self, ctx: LoanContext) -> AgentAction:
        """Deterministic mock so the service runs without an LLM provider."""
        category = ctx.item.category
        if category == "property_appraisal":
            return AgentAction(
                action_type="contact_appraiser",
                confidence=0.85,
                rationale="Appraisal discrepancy — routes to appraiser, not borrower.",
                routing_target="appraiser@example.com",
            )
        if category == "insurance":
            return AgentAction(
                action_type="schedule_renewal_reminder",
                confidence=0.7,
                rationale="Compound item (HOA renewal + HO-6). Schedule reminder and notify borrower.",
            )
        if ctx.item.previous_attempts:
            return AgentAction(
                action_type="request_document_from_borrower",
                confidence=0.9,
                rationale="Previous attempt failed; resend with explicit example.",
                draft_email="[MOCK] Hi {{name}}, ...",
            )
        return AgentAction(
            action_type="request_document_from_borrower",
            confidence=0.8,
            rationale="Standard missing-document follow-up.",
            draft_email="[MOCK] Hi {{name}}, ...",
        )

    def _action_from_response(self, message: Any) -> AgentAction:
        """Parse the first tool_use block out of an Anthropic response."""
        for block in getattr(message, "content", []):
            if getattr(block, "type", None) == "tool_use":
                return AgentAction(
                    action_type=block.name,  # type: ignore[arg-type]
                    confidence=0.8,
                    rationale=str(block.input.get("rationale", "")),
                )
        # No tool call — fall back to escalation.
        return AgentAction(
            action_type="escalate_to_human",
            confidence=0.4,
            rationale="LLM produced no tool call; routing to human review.",
        )
