#!/usr/bin/env python
"""
demo/capture_edge_cases.py
--------------------------
Captures demo/edge_cases/<case>/{input.json, expected_output.json} ONCE, fully
offline and deterministically — same pattern as capture_examples.py, but for
BOUNDARY and VALIDATION behavior rather than full end-to-end scenarios.

Two categories of case:
  - "engine"  cases call DecisionEngine directly (LeadContext in, DecisionOutput
    fields out) — for rule-boundary behavior (budget threshold, confidence gate,
    title-keyword matching).
  - "api"     cases go through the real FastAPI app (TestClient) — for request
    validation boundaries (field limits, malformed input) and the ingestion
    dedup path.

Every case's `note` field is factual, derived from actually running the case
(see demo/edge_cases/README.md for the human-readable summary). Cases marked
"known_issue" are VERIFIED CURRENT BEHAVIOR that is arguably a bug or gap —
they are captured as-is (not silently treated as correct) so a future fix
has a regression baseline to diff against.

    python demo/capture_edge_cases.py
"""
import json
import os
import sys
import tempfile
from unittest.mock import patch

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

_TMP_DB = os.path.join(tempfile.gettempdir(), "lead_edge_case_capture.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["ENVIRONMENT"] = "development"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app                           # noqa: E402
from app.agents.decision_engine import DecisionEngine, LeadContext  # noqa: E402

EDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edge_cases")


# ─────────────────────────────────────────────────────────────
# ENGINE-LEVEL CASES (DecisionEngine boundaries)
# ─────────────────────────────────────────────────────────────

ENGINE_CASES = [
    {
        "name": "budget_exactly_at_threshold",
        "note": "policy.decision.high_budget_threshold is a STRICT '>' comparison. "
                "Budget == 500000 does NOT qualify for HIGH.",
        "known_issue": False,
        "context": {"email": "a@corp.com", "budget": 500000, "job_title": "Manager", "ai_confidence": 0.9},
    },
    {
        "name": "budget_just_above_threshold",
        "note": "One unit above the threshold crosses into HIGH — confirms the boundary is exclusive.",
        "known_issue": False,
        "context": {"email": "a@corp.com", "budget": 500001, "job_title": "Manager", "ai_confidence": 0.9},
    },
    {
        "name": "confidence_exactly_at_gate",
        "note": "policy.decision.confidence_gate is a STRICT '<' comparison. Confidence == 0.70 "
                "does NOT trigger the LowConfidenceRule guardrail — it proceeds.",
        "known_issue": False,
        "context": {"email": "a@corp.com", "budget": 100, "job_title": "Manager", "ai_confidence": 0.70},
    },
    {
        "name": "confidence_just_below_gate",
        "note": "One unit below the gate triggers ESCALATE via LowConfidenceRule.",
        "known_issue": False,
        "context": {"email": "a@corp.com", "budget": 100, "job_title": "Manager", "ai_confidence": 0.6999},
    },
    {
        "name": "title_substring_false_positive_coordinator",
        "note": "KNOWN ISSUE: DecisionMakerRoutingRule matches keywords as bare substrings, not "
                "word-boundary-aware. 'Coordinator' contains 'coo' (Coo-rdinator) and is WRONGLY "
                "routed to SENIOR_SALES even though a coordinator is not a decision-maker.",
        "known_issue": True,
        "context": {"email": "a@corp.com", "budget": 100, "job_title": "Coordinator", "ai_confidence": 0.9},
    },
    {
        "name": "title_substring_false_positive_contractor",
        "note": "KNOWN ISSUE: same substring bug — 'Contractor' contains 'cto' (Contra-cto-r) and is "
                "WRONGLY routed to SENIOR_SALES.",
        "known_issue": True,
        "context": {"email": "a@corp.com", "budget": 100, "job_title": "Contractor", "ai_confidence": 0.9},
    },
    {
        "name": "title_full_ceo_not_matched",
        "note": "KNOWN GAP: a real Chief Executive Officer, when the title is spelled out in full "
                "(rather than abbreviated 'CEO'), does NOT match any keyword — 'chief executive "
                "officer' contains none of {ceo, founder, vp, director, cmo, cto, coo, president} as "
                "a substring, so this genuine decision-maker is MISSED and routed to GENERAL_SALES.",
        "known_issue": True,
        "context": {"email": "a@corp.com", "budget": 100, "job_title": "Chief Executive Officer", "ai_confidence": 0.9},
    },
    {
        "name": "title_director_correct_match",
        "note": "Control case: 'Director' is itself a configured keyword, so this match is intentional "
                "and correct (contrast with the false positives above).",
        "known_issue": False,
        "context": {"email": "a@corp.com", "budget": 100, "job_title": "Director of Sales", "ai_confidence": 0.9},
    },
    {
        "name": "spam_priority_unreachable_via_rules",
        "note": "KNOWN GAP: LeadPriority/ScoringResult both define a SPAM value, but NO rule in "
                "DecisionEngine ever assigns it — the rules engine can only produce HIGH/MEDIUM/LOW "
                "(or UNASSIGNED, defaulted to MEDIUM). SPAM is reachable only via a genuine LLM "
                "classification, never via the deterministic fallback.",
        "known_issue": True,
        "context": {"email": "a@b.com", "budget": 0, "job_title": "", "ai_confidence": 0.9},
    },
    {
        "name": "negative_budget_not_validated_internally",
        "note": "KNOWN GAP (defense-in-depth): LeadContext.budget has no ge=0 constraint (unlike the "
                "API's LeadCreateRequest, which DOES reject negative budgets with 422 — see the "
                "'negative_budget_rejected' API-level case). If any future caller builds a LeadContext "
                "directly without going through the API schema, a negative budget is silently accepted.",
        "known_issue": True,
        "context": {"email": "a@corp.com", "budget": -100, "job_title": "", "ai_confidence": 0.9},
    },
]


def run_engine_case(case: dict) -> dict:
    ctx = LeadContext(**case["context"])
    decision = DecisionEngine().process_lead(ctx)
    return {
        "case": case["name"],
        "kind": "engine",
        "known_issue": case["known_issue"],
        "note": case["note"],
        "input": case["context"],
        "output": {
            "priority": decision.priority,
            "assigned_queue": decision.assigned_queue,
            "action": decision.action,
            "halt_execution": decision.halt_execution,
            "reasoning": decision.reasoning,
        },
    }


# ─────────────────────────────────────────────────────────────
# API-LEVEL CASES (request validation + ingestion dedup)
# ─────────────────────────────────────────────────────────────

API_CASES = [
    {
        "name": "negative_budget_rejected",
        "note": "LeadCreateRequest.budget has ge=0.0 — the API layer correctly rejects a negative "
                "budget with 422 (contrast with the engine-level gap above).",
        "known_issue": False,
        "payload": {"email": "neg@corp.com", "budget": -500},
    },
    {
        "name": "malformed_email_rejected",
        "note": "EmailStr correctly rejects a non-email string with 422.",
        "known_issue": False,
        "payload": {"email": "not-an-email", "budget": 100},
    },
    {
        "name": "first_name_over_max_length",
        "note": "first_name max_length=100 — 101 characters is correctly rejected with 422.",
        "known_issue": False,
        "payload": {"email": "long@corp.com", "first_name": "A" * 101, "budget": 100},
    },
    {
        "name": "company_at_max_length_boundary",
        "note": "company max_length=200 is INCLUSIVE — exactly 200 characters is accepted (202).",
        "known_issue": False,
        "payload": {"email": "maxlen@corp.com", "company": "B" * 200, "budget": 100},
    },
    {
        "name": "company_over_max_length",
        "note": "201 characters (one over the limit) is correctly rejected with 422.",
        "known_issue": False,
        "payload": {"email": "overmax@corp.com", "company": "B" * 201, "budget": 100},
    },
    {
        "name": "minimal_payload_only_email",
        "note": "Every field except email is optional — a bare {'email': ...} payload is accepted (202).",
        "known_issue": False,
        "payload": {"email": "minimal@corp.com"},
    },
    {
        "name": "budget_zero_boundary",
        "note": "budget ge=0.0 is INCLUSIVE — exactly 0 is accepted (202).",
        "known_issue": False,
        "payload": {"email": "zerobudget@corp.com", "budget": 0},
    },
]


def run_api_case(client, headers, case: dict) -> dict:
    r = client.post("/v1/leads/", json=case["payload"], headers=headers)
    detail = None
    if r.status_code == 422:
        try:
            detail = r.json()["detail"][0]["msg"]
        except Exception:  # noqa: BLE001
            detail = r.json()
    return {
        "case": case["name"],
        "kind": "api",
        "known_issue": case["known_issue"],
        "note": case["note"],
        "input": case["payload"],
        "output": {"http_status": r.status_code, "detail": detail},
    }


def run_duplicate_case_variant(client, headers) -> dict:
    """
    KNOWN BUG: the ingestion dedup check (`Lead.email == payload.email`) compares
    against the RAW request email, but Lead.email is lowercased on write via a
    SQLAlchemy @validates hook. A same-email-different-case resubmission slips
    past the app-level dedup check, hits the DB's UNIQUE constraint on write, and
    surfaces as an unhandled 500 — not the intended clean 409 Conflict.
    """
    email_a = "Case.Test@Example.com"
    email_b = "CASE.TEST@EXAMPLE.COM"  # same address, different case
    r1 = client.post("/v1/leads/", json={"email": email_a, "budget": 1000}, headers=headers)
    r2 = client.post("/v1/leads/", json={"email": email_b, "budget": 2000}, headers=headers)
    return {
        "case": "case_variant_duplicate_email_causes_500",
        "kind": "api",
        "known_issue": True,
        "note": (
            "KNOWN BUG: submitting the same email in a different case bypasses the "
            "case-sensitive app-level dedup check. The row is stored lowercased "
            "(Lead.validate_email), so the DB's UNIQUE constraint DOES still catch "
            "the true duplicate — but only after the INSERT is attempted, so the "
            "client receives an unhandled 500 Internal Server Error instead of the "
            "intended clean 409 Conflict that a same-case resubmission gets."
        ),
        "input": {"first_submit": email_a, "second_submit_different_case": email_b},
        "output": {
            "first_submit_http_status": r1.status_code,
            "second_submit_http_status": r2.status_code,
            "second_submit_body": r2.json(),
        },
    }


# ─────────────────────────────────────────────────────────────
# CAPTURE
# ─────────────────────────────────────────────────────────────

def _write(case_result: dict):
    d = os.path.join(EDGE_DIR, case_result["case"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "input.json"), "w", encoding="utf-8") as f:
        json.dump(case_result["input"], f, indent=2)
    out = {k: v for k, v in case_result.items() if k != "input"}
    with open(os.path.join(d, "expected_output.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


def _write_readme(results: list):
    lines = [
        "# Edge Case Data — captured, verified behavior",
        "",
        "Generated by `python demo/capture_edge_cases.py`. Every row below reflects",
        "ACTUAL output from running the input through the real code (DecisionEngine",
        "directly, or the full FastAPI app via TestClient) — nothing here is guessed.",
        "",
        "`known_issue = true` rows are verified current behavior that is arguably a",
        "bug or a gap. They are captured as-is (not silently treated as correct) so a",
        "future fix has a regression baseline. See SUBMISSION_CHECKLIST.md for how",
        "this satisfies the 'input/output examples' deliverable beyond the 7 happy/",
        "failure scenarios in demo/examples/.",
        "",
        "| Case | Kind | Known issue? | Note |",
        "|---|---|---|---|",
    ]
    for r in results:
        flag = "⚠️ YES" if r["known_issue"] else "no"
        lines.append(f"| `{r['case']}` | {r['kind']} | {flag} | {r['note']} |")
    lines.append("")
    lines.append("Regenerate: `python demo/capture_edge_cases.py`. Each case's full")
    lines.append("input/output is in `demo/edge_cases/<case>/{input,expected_output}.json`.")
    with open(os.path.join(EDGE_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    print("Capturing edge-case data (offline, real code paths)...")
    results = [run_engine_case(c) for c in ENGINE_CASES]

    # The 202-accepted API cases schedule a REAL background workflow. Mock the
    # two external network seams (enrichment + LLM) so this capture stays fast,
    # deterministic, and fully offline — it doesn't change the captured
    # http_status/detail, which is decided before the background task runs.
    successful_enrichment = {
        "company_name": "Acme", "industry": "Tech", "company_size": "SMB",
        "is_freemail": False, "enrichment_failed": False,
    }
    llm_response = (
        '{"priority":"MEDIUM","confidence":0.8,"next_action":"notify","reasoning":["edge case capture"]}'
    )
    with TestClient(app, raise_server_exceptions=False) as client, \
         patch("app.agents.graph.safe_enrich_domain", return_value=successful_enrichment), \
         patch("app.agents.llm_scorer._raw_completion", return_value=llm_response):
        token = client.post(
            "/v1/auth/login", json={"username": "admin_user", "password": "password123"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        for c in API_CASES:
            results.append(run_api_case(client, headers, c))
        results.append(run_duplicate_case_variant(client, headers))

    for r in results:
        _write(r)
    _write_readme(results)

    known_issues = [r["case"] for r in results if r["known_issue"]]
    print(f"Captured {len(results)} edge cases to demo/edge_cases/ ({len(known_issues)} flagged as known issues):")
    for r in results:
        flag = "[ISSUE]" if r["known_issue"] else "[OK]   "
        print(f"  {flag} {r['case']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
