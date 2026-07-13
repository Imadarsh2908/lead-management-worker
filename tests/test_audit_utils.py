from app.utils.audit import (
    log_api_request,
    log_tool_execution,
    log_llm_call,
    log_workflow_state,
    log_escalation,
)

def test_audit_utils():
    # Verify these log wrapper calls do not raise exceptions
    log_api_request("GET", "/test", 200, 150)
    
    log_tool_execution("test_tool", {"param": 1}, {"result": "ok"})
    log_tool_execution("test_tool", {"param": 1}, error="Some tool error")
    
    log_llm_call("gpt-4", 100, 250, "test prompt summary")
    
    log_workflow_state("enrichment", "ENRICHING", 1)
    
    log_escalation("low score", 0.5)
