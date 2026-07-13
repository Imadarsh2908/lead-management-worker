"""
tests/test_resilience.py
--------------------------
Unit and integration tests for retries, circuit breakers, and fallbacks.
Verifies that the application degrades gracefully under failure conditions.
"""
import pytest
import requests
from unittest.mock import patch, Mock
from pybreaker import CircuitBreakerError, STATE_OPEN

from app.core.resilience import (
    update_crm,
    safe_update_crm,
    call_enrichment_api,
    safe_enrich_domain,
    parse_llm_json_response,
    crm_breaker,
    enrichment_breaker,
)


def test_crm_timeout_exponential_backoff_and_circuit_breaker():
    """
    Simulates a persistent CRM timeout to verify exponential backoff is triggered,
    and that the circuit breaker opens after the fail threshold is reached.
    """
    # Ensure breaker is closed before starting
    crm_breaker.close()
    
    # 1. Trigger 5 failures to hit fail_max = 5 for the breaker
    # Patch time.sleep to run backoff retries instantly
    with patch("time.sleep", return_value=None), patch("requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")
        
        for i in range(5):
            with pytest.raises(Exception):
                update_crm(f"lead-{i}", {"data": "test"})
                
        # The breaker must now be in the OPEN state
        assert crm_breaker.current_state == STATE_OPEN

        # 2. Subsequent calls should fail instantly with CircuitBreakerError without hitting requests
        with pytest.raises(CircuitBreakerError):
            update_crm("lead-blocked", {})
            
        mock_post.assert_called()
        assert mock_post.call_count == 20

    # Reset breaker for subsequent tests
    crm_breaker.close()


def test_safe_update_crm_fallbacks():
    """
    Verifies that safe_update_crm returns the correct routing instructions:
      - 'ESCALATE_TO_QUEUE' when the circuit breaker is open.
      - 'ESCALATE_TO_HUMAN' when all retries are exhausted.
      - 'SUCCESS' when it succeeds.
      - 'ESCALATE_TO_HUMAN' on unexpected exceptions.
    """
    crm_breaker.close()

    # Case A: Circuit breaker is open
    crm_breaker.open()
    result = safe_update_crm("lead-id", {"company": "Test"})
    assert result == "ESCALATE_TO_QUEUE"

    # Case B: Breaker is closed, but CRM fails all retries
    crm_breaker.close()
    with patch("app.core.resilience.update_crm") as mock_update:
        # Import Tenacity RetryError
        from tenacity import RetryError
        # Mock retry failure
        from tenacity import Future
        future = Future(1)
        future.set_exception(requests.exceptions.Timeout())
        mock_update.side_effect = RetryError(future)
        
        result = safe_update_crm("lead-id", {"company": "Test"})
        assert result == "ESCALATE_TO_HUMAN"

    # Case C: Breaker is closed, and CRM succeeds
    crm_breaker.close()
    with patch("app.core.resilience.update_crm", return_value={"status": "updated"}):
        result = safe_update_crm("lead-id", {"company": "Test"})
        assert result == "SUCCESS"

    # Case D: Unexpected error raised in CRM update
    crm_breaker.close()
    with patch("app.core.resilience.update_crm", side_effect=RuntimeError("Some raw error")):
        result = safe_update_crm("lead-id", {"company": "Test"})
        assert result == "ESCALATE_TO_HUMAN"

    crm_breaker.close()


def test_safe_enrich_domain_graceful_degradation():
    """
    When the enrichment API fails, safe_enrich_domain must degrade gracefully
    by returning an empty structure containing `enrichment_failed = True`
    rather than raising an exception.
    """
    enrichment_breaker.close()

    with patch("requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.HTTPError("Internal Server Error")
        
        result = safe_enrich_domain("failing-domain.com")
        
        # Must return fallback structure instead of throwing
        assert result["company_name"] is None
        assert result["industry"] is None
        assert result["company_size"] is None
        assert result["enrichment_failed"] is True

    enrichment_breaker.close()


def test_parse_llm_json_response_success():
    """parse_llm_json_response must clean markdown blocks and parse valid JSON."""
    raw_input = "```json\n{\n  \"status\": \"ok\",\n  \"priority\": \"HIGH\"\n}\n```"
    result = parse_llm_json_response(raw_input)
    
    assert result["data"] == {"status": "ok", "priority": "HIGH"}
    assert result["parse_error"] is None


def test_parse_llm_json_response_failure():
    """parse_llm_json_response must return the parse error message on invalid JSON."""
    raw_input = "This is not json at all."
    result = parse_llm_json_response(raw_input)
    
    assert result["data"] is None
    assert result["parse_error"] is not None
    assert "Expecting value" in result["parse_error"]


def test_validate_required_fields():
    from app.core.resilience import validate_required_fields
    # Valid payload
    assert validate_required_fields({"email": "test@domain.com"}) == {"valid": True, "missing": []}
    # Invalid payload (missing email)
    assert validate_required_fields({}) == {"valid": False, "missing": ["email"]}


@patch("requests.get")
def test_update_crm_success(mock_get):
    from app.core.resilience import update_crm
    mock_resp = Mock()
    mock_resp.json.return_value = {"status": "ok"}
    mock_resp.raise_for_status.return_value = None
    
    with patch("requests.post", return_value=mock_resp):
        res = update_crm("123", {"data": 1})
        assert res == {"status": "ok"}


@patch("requests.get")
def test_call_enrichment_api_success(mock_get):
    from app.core.resilience import call_enrichment_api
    mock_resp = Mock()
    mock_resp.json.return_value = {"name": "Test"}
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp
    
    res = call_enrichment_api("test.com")
    assert res == {"name": "Test"}

