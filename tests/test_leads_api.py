"""
tests/test_leads_api.py
-------------------------
Integration tests for the Lead Management API endpoints.
Tests validation, deduplication, pagination, and soft deletion.
"""
import uuid
from unittest.mock import patch
import pytest

from app.models.lead import Lead, WorkflowState, WorkflowStatus


def test_create_lead_success(client, db_session):
    """
    POST /v1/leads/ must create a lead record, initialize workflow state,
    and trigger the autonomous worker in the background.
    """
    payload = {
        "email": "integration@test.com",
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+1234567890",
        "company": "Tech Corp",
        "job_title": "Director of Engineering",
        "budget": 250000.0,
    }

    # Patch BackgroundTasks to prevent executing the real LangGraph workflow
    with patch("fastapi.BackgroundTasks.add_task") as mock_add_task:
        response = client.post("/v1/leads/", json=payload)
        
        assert response.status_code == 202
        data = response.json()
        assert data["email"] == "integration@test.com"
        assert data["first_name"] == "John"
        assert data["company"] == "Tech Corp"
        assert data["priority"] == "UNASSIGNED"
        assert "id" in data

        # Verify database record exists
        lead_id = uuid.UUID(data["id"])
        db_lead = db_session.query(Lead).filter(Lead.id == lead_id).first()
        assert db_lead is not None
        assert db_lead.email == "integration@test.com"

        # Verify workflow state was initialized to RECEIVED
        wf_state = db_session.query(WorkflowState).filter(WorkflowState.lead_id == lead_id).first()
        assert wf_state is not None
        assert wf_state.current_status == WorkflowStatus.RECEIVED

        # Verify BackgroundTasks.add_task was called
        mock_add_task.assert_called_once()
        args, kwargs = mock_add_task.call_args
        assert kwargs["lead_id"] == str(lead_id)
        assert kwargs["lead_payload"] == payload


def test_create_lead_duplicate(client, db_session):
    """
    POST /v1/leads/ must return a 409 Conflict if email is already taken.
    """
    # Seed a lead in DB
    existing_lead = Lead(email="dup@test.com", first_name="Existing")
    db_session.add(existing_lead)
    db_session.commit()

    payload = {"email": "dup@test.com", "first_name": "New Person"}
    response = client.post("/v1/leads/", json=payload)
    
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_lead_invalid_email(client):
    """
    POST /v1/leads/ must return 422 validation error for invalid emails.
    """
    payload = {"email": "not_an_email_at_all"}
    response = client.post("/v1/leads/", json=payload)
    
    assert response.status_code == 422
    assert "valid email" in response.text


def test_get_lead_by_id_success(client, db_session):
    """
    GET /v1/leads/{id} must return the correct lead record.
    """
    lead = Lead(email="get@test.com", first_name="Get", last_name="Test")
    db_session.add(lead)
    db_session.commit()

    response = client.get(f"/v1/leads/{lead.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "get@test.com"
    assert data["first_name"] == "Get"


def test_get_lead_by_id_not_found(client):
    """
    GET /v1/leads/{id} must return 404 for non-existent IDs.
    """
    fake_uuid = str(uuid.uuid4())
    response = client.get(f"/v1/leads/{fake_uuid}")
    assert response.status_code == 404


def test_get_lead_status_success(client, db_session):
    """
    GET /v1/leads/{id}/status must return workflow status details.
    """
    lead = Lead(email="status@test.com")
    db_session.add(lead)
    db_session.flush()

    wf_state = WorkflowState(lead_id=lead.id, current_status=WorkflowStatus.ENRICHING)
    db_session.add(wf_state)
    db_session.commit()

    response = client.get(f"/v1/leads/{lead.id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ENRICHING"
    assert data["retry_count"] == 0


def test_list_leads_paginated(client, db_session):
    """
    GET /v1/leads/ must return a paginated list of leads.
    """
    # Seed 3 leads
    db_session.add_all([
        Lead(email="list1@test.com"),
        Lead(email="list2@test.com"),
        Lead(email="list3@test.com"),
    ])
    db_session.commit()

    response = client.get("/v1/leads/?page=1&page_size=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2


def test_delete_lead_soft_delete(client, db_session):
    """
    DELETE /v1/leads/{id} must mark the record as is_deleted=True and return 204.
    """
    lead = Lead(email="del@test.com")
    db_session.add(lead)
    db_session.commit()

    # Call DELETE (overridden as Admin in conftest.py)
    response = client.delete(f"/v1/leads/{lead.id}")
    assert response.status_code == 204

    # Refresh lead from database to get the updated status committed by the client session
    db_session.refresh(lead)
    assert lead.is_deleted is True

    # Calling GET should now return 404 because GET filters active records
    response_get = client.get(f"/v1/leads/{lead.id}")
    assert response_get.status_code == 404
