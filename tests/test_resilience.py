
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
    call_llm_completion,
    _get_llm_breaker,
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


def test_llm_breaker_is_per_model_not_shared():
    """
    REGRESSION: the LLM circuit breaker used to be a single instance shared by
    every model in the fallback chain, so a failing PRIMARY model would trip
    it and then block calls to the FALLBACK model too — even though the
    fallback model never got a real chance to run (this is what production
    was observed doing: both models in the chain failing with the identical
    "CircuitBreakerError: Timeout not elapsed yet" message). Each model must
    get its own independent breaker.
    """
    import httpx
    from openai import APIConnectionError
    from app.core.policy import get_policy

    bad_model = "bad-model-regression-test"
    good_model = "good-model-regression-test"
    fail_max = get_policy().resilience.llm_breaker.fail_max

    # Fresh breaker state for these test-only model names.
    _get_llm_breaker(bad_model).close()
    _get_llm_breaker(good_model).close()

    bad_client = Mock()
    bad_client.chat.completions.create.side_effect = APIConnectionError(
        request=httpx.Request("POST", "https://example.invalid")
    )

    with patch("time.sleep", return_value=None):
        # Trip the BAD model's breaker. pybreaker raises CircuitBreakerError
        # (not the original exception) specifically for the call that crosses
        # the failure threshold, so accept either on each iteration.
        for _ in range(fail_max):
            with pytest.raises((APIConnectionError, CircuitBreakerError)):
                call_llm_completion(bad_client, bad_model, [{"role": "user", "content": "hi"}], timeout_seconds=5)

    assert _get_llm_breaker(bad_model).current_state == STATE_OPEN

    # Further calls to the BAD model fail instantly via its own breaker.
    with pytest.raises(CircuitBreakerError):
        call_llm_completion(bad_client, bad_model, [{"role": "user", "content": "hi"}], timeout_seconds=5)

    # A DIFFERENT model must be completely unaffected and succeed normally.
    good_client = Mock()
    good_client.chat.completions.create.return_value = Mock(
        message=None, choices=[Mock(message=Mock(content='{"ok": true}'))]
    )
    result = call_llm_completion(good_client, good_model, [{"role": "user", "content": "hi"}], timeout_seconds=5)
    assert result == '{"ok": true}'
    assert _get_llm_breaker(good_model).current_state != STATE_OPEN

    _get_llm_breaker(bad_model).close()
    _get_llm_breaker(good_model).close()


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


def test_parse_llm_json_response_strip_bug_regression():
    """
    Regression for the `.strip("```json")` bug: that call treated its argument as
    a SET OF CHARACTERS and greedily ate any of {'`','j','s','o','n'} from both
    ends, corrupting valid payloads. The fixed parser removes fences as exact
    substrings, so:
      - keys/values containing 'json' survive intact, and
      - a bare scalar beginning with a stripped char (e.g. `null`) is not mangled.
    """
    # 1. Key literally named "json_data" must survive, fenced or not.
    fenced = '```json\n{"json_data": "season-of-json", "priority": "LOW"}\n```'
    res = parse_llm_json_response(fenced)
    assert res["parse_error"] is None
    assert res["data"]["json_data"] == "season-of-json"
    assert res["data"]["priority"] == "LOW"

    plain = '{"json_data": 42, "note": "nojson"}'
    res = parse_llm_json_response(plain)
    assert res["data"] == {"json_data": 42, "note": "nojson"}

    # 2. The crisp failure of the old set-strip: leading 'n' of `null` was eaten,
    #    yielding invalid `ull`. The fixed parser handles it correctly.
    res = parse_llm_json_response("null")
    assert res["parse_error"] is None
    assert res["data"] is None

    # 3. Fence without a language tag still works.
    res = parse_llm_json_response('```\n{"a": 1}\n```')
    assert res["data"] == {"a": 1}


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

