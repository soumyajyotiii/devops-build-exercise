"""Trivial smoke test — proves the app boots and the mock agent path runs.

The candidate is welcome to keep this as part of CI but not expected to
expand the test suite. The point is that `pytest` exits 0 in their
pipeline, not that this is a real test bench.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent.main import app

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_process_item_mock_path() -> None:
    payload = {
        "loan_id": "SAAF-2026-04892",
        "loan_type": "DSCR",
        "borrower": {
            "id": "BRW-7291",
            "name": "Michael Reeves",
            "email": "m.reeves@investormail.com",
            "entity": "Reeves Capital Holdings LLC",
        },
        "item": {
            "id": "ITEM-001",
            "category": "income_verification",
            "priority": "high",
            "status": "outstanding",
            "source": "underwriter",
            "underwriter_notes": "Need February 2026 bank statement, all pages.",
            "documents_on_file": [],
            "previous_attempts": [],
        },
        "timeline_pressure": {"days_until_rate_lock_expiry": 17, "risk_level": "moderate"},
    }

    r = client.post("/v1/items/process", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["loan_id"] == "SAAF-2026-04892"
    assert body["item_id"] == "ITEM-001"
    assert body["action"]["action_type"] in {
        "request_document_from_borrower",
        "contact_appraiser",
        "schedule_renewal_reminder",
        "escalate_to_human",
    }
    assert body["audit_id"].startswith("audit_")
