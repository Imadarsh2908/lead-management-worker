"""
tests/test_demo_seams.py
------------------------
Locks in the env-guarded demo seams added for the demo harness, and a regression
test for the GUARDRAIL_OVERRIDE audit enum (a lead with a guardrail override used
to lose its entire audit trail on read-back).
"""
import uuid

from app.models.lead import Lead, AuditLog, AuditActionType


def test_raw_endpoint_is_inert_outside_development(client):
    """
    The /v1/leads/raw demo seam must behave as if it doesn't exist unless
    ENVIRONMENT == 'development'. The test env is 'testing', so even with the
    X-Demo-Raw header it must 404.
    """
    resp = client.post(
        "/v1/leads/raw",
        json={"first_name": "NoEmail", "company": "Acme"},
        headers={"X-Demo-Raw": "true"},
    )
    assert resp.status_code == 404


def test_guardrail_override_enum_roundtrips(db_session):
    """
    Regression: node_lead_score writes action_type='GUARDRAIL_OVERRIDE'. That
    value must be a valid AuditActionType so the row persists AND deserializes on
    read (previously it wrote leniently but raised LookupError on read, rolling
    back the whole single-transaction audit write).
    """
    assert AuditActionType("GUARDRAIL_OVERRIDE") is AuditActionType.GUARDRAIL_OVERRIDE

    lead_id = uuid.uuid4()
    db_session.add(Lead(id=lead_id, email="override@test.com"))
    db_session.add(AuditLog(
        lead_id=lead_id,
        action_type="GUARDRAIL_OVERRIDE",
        message="Priority downgraded by rule engine: HIGH -> LOW",
    ))
    db_session.commit()
    db_session.expire_all()

    row = db_session.query(AuditLog).filter(AuditLog.lead_id == lead_id).one()
    assert row.action_type == AuditActionType.GUARDRAIL_OVERRIDE
