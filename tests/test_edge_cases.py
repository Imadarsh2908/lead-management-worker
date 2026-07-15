"""
tests/test_edge_cases.py
------------------------
Regression tests for the boundary/edge-case data captured in demo/edge_cases/
(see demo/capture_edge_cases.py and demo/edge_cases/README.md for the full
input/output set and how it was generated).

Two kinds of assertion here:
  - CORRECT boundary behavior (budget threshold, confidence gate, field-length
    limits) — these must keep passing; a failure means a real regression.
  - KNOWN ISSUES (title substring false positives/negatives, SPAM unreachable
    via rules, case-variant duplicate email -> 500) — these assert TODAY'S
    verified behavior, not the ideal behavior. They exist so a future fix
    changes an assertion here deliberately, instead of silently drifting.
"""
import uuid

from fastapi.testclient import TestClient

from app.agents.decision_engine import DecisionEngine, LeadContext
from app.main import app


def _decide(**kw):
    return DecisionEngine().process_lead(LeadContext(**kw))


# ─────────────────────────────────────────────────────────────
# Correct boundary behavior — must keep passing
# ─────────────────────────────────────────────────────────────

def test_budget_exactly_at_threshold_is_not_high():
    """policy.decision.high_budget_threshold is a strict '>' — the boundary value itself doesn't qualify."""
    d = _decide(email="a@corp.com", budget=500_000, job_title="Manager", ai_confidence=0.9)
    assert d.priority == "MEDIUM"


def test_budget_just_above_threshold_is_high():
    d = _decide(email="a@corp.com", budget=500_001, job_title="Manager", ai_confidence=0.9)
    assert d.priority == "HIGH"


def test_confidence_exactly_at_gate_proceeds():
    """policy.decision.confidence_gate is a strict '<' — exactly at the gate does NOT escalate."""
    d = _decide(email="a@corp.com", budget=100, job_title="Manager", ai_confidence=0.70)
    assert d.action != "ESCALATE"
    assert d.halt_execution is False


def test_confidence_just_below_gate_escalates():
    d = _decide(email="a@corp.com", budget=100, job_title="Manager", ai_confidence=0.6999)
    assert d.action == "ESCALATE"
    assert d.halt_execution is True


def test_title_director_is_a_correct_match():
    """Control case: 'director' IS a configured keyword, so this match is intentional."""
    d = _decide(email="a@corp.com", budget=100, job_title="Director of Sales", ai_confidence=0.9)
    assert d.assigned_queue == "SENIOR_SALES"


def test_api_negative_budget_rejected(client):
    resp = client.post(
        "/v1/leads/",
        json={"email": f"neg-{uuid.uuid4().hex[:8]}@corp.com", "budget": -500},
    )
    assert resp.status_code == 422


def test_api_malformed_email_rejected(client):
    resp = client.post("/v1/leads/", json={"email": "not-an-email", "budget": 100})
    assert resp.status_code == 422


def test_api_first_name_over_max_length_rejected(client):
    resp = client.post(
        "/v1/leads/",
        json={"email": f"long-{uuid.uuid4().hex[:8]}@corp.com", "first_name": "A" * 101, "budget": 100},
    )
    assert resp.status_code == 422


def test_api_company_at_max_length_boundary_accepted(client):
    """company max_length=200 is inclusive."""
    resp = client.post(
        "/v1/leads/",
        json={"email": f"maxlen-{uuid.uuid4().hex[:8]}@corp.com", "company": "B" * 200, "budget": 100},
    )
    assert resp.status_code == 202


def test_api_company_over_max_length_rejected(client):
    resp = client.post(
        "/v1/leads/",
        json={"email": f"overmax-{uuid.uuid4().hex[:8]}@corp.com", "company": "B" * 201, "budget": 100},
    )
    assert resp.status_code == 422


def test_api_minimal_payload_only_email_accepted(client):
    resp = client.post("/v1/leads/", json={"email": f"minimal-{uuid.uuid4().hex[:8]}@corp.com"})
    assert resp.status_code == 202


def test_api_budget_zero_boundary_accepted(client):
    """budget ge=0.0 is inclusive."""
    resp = client.post(
        "/v1/leads/", json={"email": f"zerobudget-{uuid.uuid4().hex[:8]}@corp.com", "budget": 0}
    )
    assert resp.status_code == 202


# ─────────────────────────────────────────────────────────────
# KNOWN ISSUES — verified CURRENT behavior, not necessarily desired.
# See demo/edge_cases/README.md for the full explanation of each.
# ─────────────────────────────────────────────────────────────

def test_KNOWN_ISSUE_title_substring_false_positive_coordinator():
    """'Coordinator' contains 'coo' and is wrongly routed to SENIOR_SALES."""
    d = _decide(email="a@corp.com", budget=100, job_title="Coordinator", ai_confidence=0.9)
    assert d.assigned_queue == "SENIOR_SALES"  # false positive, not a real decision-maker


def test_KNOWN_ISSUE_title_substring_false_positive_contractor():
    """'Contractor' contains 'cto' and is wrongly routed to SENIOR_SALES."""
    d = _decide(email="a@corp.com", budget=100, job_title="Contractor", ai_confidence=0.9)
    assert d.assigned_queue == "SENIOR_SALES"  # false positive


def test_KNOWN_ISSUE_full_ceo_title_not_matched():
    """A real CEO with the title spelled out in full is MISSED (no 'ceo' substring)."""
    d = _decide(email="a@corp.com", budget=100, job_title="Chief Executive Officer", ai_confidence=0.9)
    assert d.assigned_queue == "GENERAL_SALES"  # should be SENIOR_SALES; genuine gap


def test_KNOWN_ISSUE_spam_priority_unreachable_via_rules():
    """No rule in DecisionEngine ever assigns SPAM — only the LLM path can."""
    d = _decide(email="a@b.com", budget=0, job_title="", ai_confidence=0.9)
    assert d.priority != "SPAM"
    assert d.priority == "MEDIUM"  # falls through to the UNASSIGNED->MEDIUM default


def test_KNOWN_ISSUE_negative_budget_not_validated_at_engine_level():
    """LeadContext.budget has no ge=0 guard (unlike the API schema) — accepted silently."""
    d = _decide(email="a@corp.com", budget=-100, job_title="", ai_confidence=0.9)
    assert d.priority == "MEDIUM"  # did not raise, did not affect priority


def test_KNOWN_ISSUE_case_variant_duplicate_email_causes_500(client):
    """
    Same email, different case, bypasses the case-sensitive app-level dedup
    check and hits the DB's UNIQUE constraint on write -> unhandled 500,
    instead of the clean 409 a same-case resubmission gets.

    Uses a local TestClient with raise_server_exceptions=False (unlike the
    shared `client` fixture) so the unhandled IntegrityError surfaces as the
    real HTTP 500 response a deployed server would send, instead of propagating
    as a raw Python exception in the test process. It wraps the SAME `app`
    object, so the `client` fixture's dependency overrides (db/auth) still
    apply — `client` is requested only for that setup side effect, not called
    directly.
    """
    token = uuid.uuid4().hex[:8]
    email_a = f"Case.{token}@Example.com"
    email_b = f"CASE.{token}@EXAMPLE.COM"  # same address, different case

    with TestClient(app, raise_server_exceptions=False) as lenient_client:
        r1 = lenient_client.post("/v1/leads/", json={"email": email_a, "budget": 1000})
        assert r1.status_code == 202

        r2 = lenient_client.post("/v1/leads/", json={"email": email_b, "budget": 2000})
        assert r2.status_code == 500  # NOT the intended 409 — see README known-issue note
