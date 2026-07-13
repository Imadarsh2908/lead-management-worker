import pytest
from unittest.mock import patch, Mock
import requests
from pydantic import BaseModel, Field, ValidationError
from tenacity import RetryError

from app.agents.tools.base import BaseAgentTool
from app.agents.tools.enrich_domain import EnrichDomainTool, EnrichDomainInput, EnrichDomainOutput


# ─────────────────────────────────────────────────────────────
# DUMMY SCHEMAS & TOOL FOR TESTING BASEAGENTTOOL
# ─────────────────────────────────────────────────────────────

class DummyInput(BaseModel):
    value: str

class DummyOutput(BaseModel):
    result: str

class WrongOutput(BaseModel):
    other_result: str

class DummyTool(BaseAgentTool):
    name: str = "dummy_tool"
    description: str = "A dummy tool for testing base functionalities."
    args_schema: type[BaseModel] = DummyInput
    response_schema: type[BaseModel] = DummyOutput

    def _execute_tool(self, value: str) -> BaseModel:
        if value == "raise_error":
            raise ValueError("Custom business error")
        elif value == "return_wrong_type":
            return WrongOutput(other_result="wrong")
        return DummyOutput(result=value)


# ─────────────────────────────────────────────────────────────
# TESTS FOR BASEAGENTTOOL
# ─────────────────────────────────────────────────────────────

def test_base_tool_success():
    tool = DummyTool()
    # Invoke uses the _run wrapper internally
    response_str = tool.invoke({"value": "hello"})
    assert "hello" in response_str
    assert "result" in response_str


def test_base_tool_business_error():
    tool = DummyTool()
    response_str = tool.invoke({"value": "raise_error"})
    assert "error" in response_str
    assert "Custom business error" in response_str


def test_base_tool_type_error():
    tool = DummyTool()
    response_str = tool.invoke({"value": "return_wrong_type"})
    assert "error" in response_str
    assert "returned WrongOutput, expected DummyOutput" in response_str


# ─────────────────────────────────────────────────────────────
# TESTS FOR ENRICHDOMAINTOOL
# ─────────────────────────────────────────────────────────────

def test_enrich_domain_input_validation():
    tool = EnrichDomainTool()
    # Invalid email must fail validation and raise ValidationError
    with pytest.raises(ValidationError):
        tool.invoke({"email": "not-a-valid-email"})


def test_enrich_domain_freemail():
    tool = EnrichDomainTool()
    with patch.object(tool, "_call_enrichment_api") as mock_api:
        response_str = tool.invoke({"email": "test@gmail.com"})
        mock_api.assert_not_called()
        
        # Output should parse to EnrichDomainOutput
        output = EnrichDomainOutput.model_validate_json(response_str)
        assert output.domain == "gmail.com"
        assert output.is_freemail is True
        assert output.company_name is None
        assert output.enrichment_failed is False


@patch("app.agents.tools.enrich_domain.requests.get")
def test_enrich_domain_b2b_success(mock_get):
    # Mocking standard response
    mock_resp = Mock()
    mock_resp.json.return_value = {"name": "Stark Industries", "industry": "Defense", "size": "Enterprise"}
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    tool = EnrichDomainTool()
    response_str = tool.invoke({"email": "tony@stark.com"})
    
    # Verify API request details
    mock_get.assert_called_once_with("https://api.example-enrichment.com/v1/company?domain=stark.com", timeout=5.0)
    
    output = EnrichDomainOutput.model_validate_json(response_str)
    assert output.domain == "stark.com"
    assert output.is_freemail is False
    assert output.company_name == "Stark Industries"
    assert output.industry == "Defense"
    assert output.company_size == "Enterprise"
    assert output.enrichment_failed is False


@patch("app.agents.tools.enrich_domain.requests.get")
@patch("time.sleep", return_value=None)  # Avoid actual waiting in tests
def test_enrich_domain_b2b_retry_and_graceful_degradation(mock_sleep, mock_get):
    # Mocking timeouts for all retries
    mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")

    tool = EnrichDomainTool()
    response_str = tool.invoke({"email": "info@startup.io"})
    
    # 3 attempts (stop_after_attempt(3))
    assert mock_get.call_count == 3
    
    output = EnrichDomainOutput.model_validate_json(response_str)
    assert output.domain == "startup.io"
    assert output.is_freemail is False
    assert output.company_name is None
    assert output.enrichment_failed is True
