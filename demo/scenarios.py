"""
demo/scenarios.py
-----------------
Single source of truth for every demo scenario: the payload, which requirement
it maps to, what it demonstrates, the endpoint it uses, the server env it needs,
and the expected outcome.

Both the live HTTP harness (run_demo.py) and the offline fixture capture
(capture_examples.py) import from here so the two never drift.
"""
from typing import Dict, Any


# Each scenario is a plain dict so it round-trips to JSON cleanly.
#   requirement   — short label of the brief requirement it maps to
#   explanation   — one paragraph printed before the run
#   endpoint      — "standard" (POST /v1/leads/) or "raw" (POST /v1/leads/raw, demo seam)
#   payload       — the lead body to submit
#   expect_status — the final workflow status we assert (COMPLETED | ESCALATED)
#   expect_http   — expected HTTP status of the submit (202 normally, 409 for dup)
#   audit_contains— substrings we expect to see somewhere in the audit trail
#   server_env    — env the SERVER must run with for this scenario to behave as described
#   unique_email  — if True, the harness appends a per-run token to keep re-runs idempotent
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "happy_path": {
        "requirement": "R1 — Autonomous end-to-end lead processing & scoring",
        "explanation": (
            "A complete, high-value enterprise lead flows through the entire pipeline "
            "with no human in the loop: validate → CRM lookup → enrichment → LLM score "
            "→ decision → follow-up draft → notify → audit. The LLM scores it HIGH with "
            "high confidence, the rule guardrail agrees, and the worker autonomously "
            "drafts a personalized follow-up. Expected final status: COMPLETED, HIGH."
        ),
        "endpoint": "standard",
        "payload": {
            "email": "jane.doe@globex.com",
            "first_name": "Jane", "last_name": "Doe",
            "company": "Globex Corporation",
            "job_title": "VP of Engineering",
            "budget": 750000,
        },
        "expect_status": "COMPLETED",
        "expect_http": 202,
        "audit_contains": ["generate_follow_up"],
        "server_env": {"LLM_ENABLED": "true", "LLM_API_KEY": "<your OpenRouter key>"},
        "unique_email": True,
    },
    "missing_email": {
        "requirement": "R2 — Escalate on missing contact info (guardrail)",
        "explanation": (
            "A lead arrives with NO email. The strict API schema requires one, so this "
            "uses the env-guarded demo seam (POST /v1/leads/raw + header X-Demo-Raw: true, "
            "development only) to submit the raw payload. The graph's validate node detects "
            "the missing required field and escalates immediately — the worker never guesses "
            "who to contact. Expected final status: ESCALATED."
        ),
        "endpoint": "raw",
        "payload": {
            "first_name": "Alex", "company": "Acme Robotics",
            "job_title": "Head of Procurement", "budget": 120000,
        },
        "expect_status": "ESCALATED",
        "expect_http": 202,
        "audit_contains": ["Validation", "ESCALAT"],
        "server_env": {"ENVIRONMENT": "development"},
        "unique_email": False,
    },
    "enrichment_down": {
        "requirement": "R3 — Degrade, don't fabricate (graceful degradation + bounded retries)",
        "explanation": (
            "The enrichment API is unreachable (ENRICHMENT_API_URL pointed at "
            "http://127.0.0.1:9, which refuses connections). The worker retries with "
            "backoff up to the configured ceiling, then PROCEEDS with degraded data "
            "(enrichment_failed=True) rather than escalating — missing company data is not "
            "invented. Scoring continues on the remaining signals. Expected final status: "
            "COMPLETED, with enrichment_failed=True visible in the audit trail."
        ),
        "endpoint": "standard",
        "payload": {
            "email": "dana@initech.com",
            "first_name": "Dana", "company": "Initech",
            "job_title": "Director of Operations", "budget": 600000,
        },
        "expect_status": "COMPLETED",
        "expect_http": 202,
        # The reliable, model-agnostic signal is enrichment_failed=True in the
        # audit trail. (We intentionally do NOT assert on LLM reasoning wording
        # like "degraded" — that varies by model and would make the check flaky.)
        "audit_contains": ["enrichment_failed"],
        "server_env": {"ENRICHMENT_API_URL": "http://127.0.0.1:9", "LLM_ENABLED": "true"},
        "unique_email": True,
    },
    "llm_malformed": {
        "requirement": "R4 — Structured-output resilience (self-correcting re-prompt)",
        "explanation": (
            "The model's FIRST response is malformed JSON (forced via LLM_FORCE_MALFORMED). "
            "The parser fails, the worker re-prompts once with the exact parse error, and the "
            "second response parses cleanly. The audit trail records source=llm_selfcorrected. "
            "Expected final status: COMPLETED."
        ),
        "endpoint": "standard",
        "payload": {
            "email": "sam@umbrella.com",
            "first_name": "Sam", "company": "Umbrella Inc",
            "job_title": "CTO", "budget": 550000,
        },
        "expect_status": "COMPLETED",
        "expect_http": 202,
        "audit_contains": ["llm_selfcorrected"],
        "server_env": {"LLM_FORCE_MALFORMED": "true", "LLM_ENABLED": "true"},
        "unique_email": True,
    },
    "llm_dead": {
        "requirement": "R5 — Graceful degradation to rules + confidence gate",
        "explanation": (
            "The LLM is disabled entirely (LLM_ENABLED=false). The worker falls back to the "
            "deterministic rule engine and stamps confidence=0.50 — below the configured "
            "confidence gate — so it routes to human escalation by design (source=rules_fallback). "
            "This proves a total model outage degrades safely instead of acting blindly. "
            "Expected final status: ESCALATED."
        ),
        "endpoint": "standard",
        "payload": {
            "email": "pat@wayne.com",
            "first_name": "Pat", "company": "Wayne Enterprises",
            "job_title": "Procurement Lead", "budget": 300000,
        },
        "expect_status": "ESCALATED",
        "expect_http": 202,
        "audit_contains": ["rules_fallback"],
        "server_env": {"LLM_ENABLED": "false"},
        "unique_email": True,
    },
    "low_confidence": {
        "requirement": "R6 — Confidence gate / human-in-the-loop escalation",
        "explanation": (
            "An ambiguous lead (no budget, vague/blank title, unknown domain, enrichment "
            "unavailable) gives the model too little signal, so it returns LOW confidence. "
            "Confidence below the gate routes to human escalation. Because a live model can be "
            "over-confident, the demo pins confidence via the env-guarded knob "
            "LLM_FORCE_CONFIDENCE (development only). Expected final status: ESCALATED."
        ),
        "endpoint": "standard",
        "payload": {
            "email": "info@unknown-startup.io",
            "company": "unknown-startup.io", "job_title": "", "budget": 0,
        },
        "expect_status": "ESCALATED",
        "expect_http": 202,
        "audit_contains": ["confidence"],
        "server_env": {"LLM_FORCE_CONFIDENCE": "0.40", "ENVIRONMENT": "development", "LLM_ENABLED": "true"},
        "unique_email": True,
    },
    "duplicate_lead": {
        "requirement": "R7 — Deduplication / idempotent ingestion",
        "explanation": (
            "The same email is submitted twice. In the CURRENT API design, ingestion performs "
            "an email-uniqueness check and rejects the second submit with HTTP 409 Conflict — "
            "BEFORE the workflow starts. (The graph also has a CRM-skip branch that sets "
            "exists_in_crm=True, but the API-level 409 short-circuits before that path is "
            "reached, so 409 is what you observe.) Expected: first submit 202, second submit 409."
        ),
        "endpoint": "standard",
        "payload": {
            "email": "dup@example.com",
            "first_name": "Dup", "company": "Doublecorp",
            "job_title": "Manager", "budget": 80000,
        },
        "expect_status": "COMPLETED",   # first lead completes; the 409 is the headline assertion
        "expect_http": 202,
        "expect_conflict_on_resubmit": True,
        "audit_contains": [],
        "server_env": {"LLM_ENABLED": "true"},
        "unique_email": True,
    },
}


def scenario_names():
    return list(SCENARIOS.keys())
