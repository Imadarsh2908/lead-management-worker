#!/usr/bin/env python
"""
demo/capture_examples.py
------------------------
Captures demo/examples/<scenario>/{input.json, expected_output.json} ONCE, fully
offline and deterministically.

Why not just run_demo.py --capture? Because several scenarios need a live LLM /
network / specific server env. This harness drives the REAL FastAPI app in-process
via Starlette's TestClient over an isolated SQLite DB, mocking only the two
external seams (the LLM network call and the enrichment API). Everything else —
routing, guardrails, audit persistence, the /audit endpoint — is the real code
path, so the captured fixtures reflect genuine behavior.

    python demo/capture_examples.py

Re-run any time to regenerate the fixtures.
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

# Configure the environment BEFORE importing the app (settings is read at import).
_TMP_DB = os.path.join(tempfile.gettempdir(), "lead_demo_capture.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["ENVIRONMENT"] = "development"   # enables table creation + demo seams

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app                           # noqa: E402
from app.core.config import settings               # noqa: E402
from demo.scenarios import SCENARIOS               # noqa: E402
from demo.run_demo import _trim_audit              # noqa: E402  (single trimming impl)

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")

# Canned, valid model outputs per scenario (JSON strings the parser will accept).
_LLM_OK = {
    "happy_path": '{"priority":"HIGH","confidence":0.93,"next_action":"generate_follow_up",'
                  '"reasoning":["Budget exceeds the configured threshold","VP is a senior decision-maker","Enterprise corporate domain"]}',
    "enrichment_down": '{"priority":"HIGH","confidence":0.82,"next_action":"generate_follow_up",'
                       '"reasoning":["Budget exceeds threshold","Enrichment failed - company size unknown; scored on budget and title"]}',
    "llm_malformed": '{"priority":"HIGH","confidence":0.80,"next_action":"generate_follow_up",'
                     '"reasoning":["Recovered after a self-correcting re-prompt"]}',
    "low_confidence": '{"priority":"MEDIUM","confidence":0.90,"next_action":"notify",'
                      '"reasoning":["Signals are weak but present"]}',
    "duplicate_lead": '{"priority":"MEDIUM","confidence":0.78,"next_action":"notify","reasoning":["Mid-market lead"]}',
}
_ENRICH_OK = {"company_name": "Acme", "industry": "Software", "company_size": "Enterprise",
              "is_freemail": False, "enrichment_failed": False}
_ENRICH_FAIL = {"company_name": None, "industry": None, "company_size": None,
                "is_freemail": False, "enrichment_failed": True}


def _reset_llm_knobs():
    settings.LLM_ENABLED = True
    settings.LLM_FORCE_MALFORMED = False
    settings.LLM_FORCE_CONFIDENCE = None


def _login(client) -> str:
    r = client.post("/v1/auth/login", json={"username": "admin_user", "password": "password123"})
    r.raise_for_status()
    return r.json()["access_token"]


def _submit(client, token, sc, payload):
    headers = {"Authorization": f"Bearer {token}"}
    if sc["endpoint"] == "raw":
        headers["X-Demo-Raw"] = "true"
        return client.post("/v1/leads/raw", json=payload, headers=headers)
    return client.post("/v1/leads/", json=payload, headers=headers)


def _capture(scenario_key, payload, final, audit, extra=None):
    d = os.path.join(EXAMPLES_DIR, scenario_key)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "input.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    out = {"scenario": scenario_key, "final_status": final, "audit": _trim_audit(audit)}
    if extra:
        out.update(extra)
    with open(os.path.join(d, "expected_output.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


def run_one(client, token, key):
    sc = SCENARIOS[key]
    payload = dict(sc["payload"])
    _reset_llm_knobs()

    # Per-scenario external-seam mocks + knobs (mirrors each scenario's mechanism).
    enrich = _ENRICH_OK
    llm_raw = _LLM_OK.get(key, _LLM_OK["duplicate_lead"])
    if key == "enrichment_down":
        enrich = _ENRICH_FAIL
    elif key == "llm_malformed":
        settings.LLM_FORCE_MALFORMED = True
    elif key == "llm_dead":
        settings.LLM_ENABLED = False
    elif key == "low_confidence":
        settings.LLM_FORCE_CONFIDENCE = 0.40
        enrich = _ENRICH_FAIL

    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.agents.graph.safe_enrich_domain", return_value=enrich), \
         patch("app.agents.llm_scorer._raw_completion", return_value=llm_raw):
        resp = _submit(client, token, sc, payload)
        extra = None
        if sc.get("expect_conflict_on_resubmit"):
            lead_id = resp.json()["id"]
            dup = _submit(client, token, sc, payload)
            extra = {"resubmit_http": dup.status_code}
        else:
            lead_id = resp.json()["id"]

    status = client.get(f"/v1/leads/{lead_id}/status", headers=headers).json()["status"]
    audit = client.get(f"/v1/leads/{lead_id}/audit", headers=headers).json()
    _capture(key, payload, status, audit, extra)
    ok = status == sc["expect_status"]
    flag = "[OK]  " if ok else "[FAIL]"
    extra_str = f" | resubmit={extra['resubmit_http']}" if extra else ""
    print(f"  {flag} {key:<16} -> {status} (expected {sc['expect_status']}){extra_str}")
    return ok


def main():
    print("Capturing demo example fixtures (offline, in-process)…")
    _reset_llm_knobs()
    with TestClient(app) as client:
        token = _login(client)
        results = [run_one(client, token, key) for key in SCENARIOS]
    _reset_llm_knobs()
    print(f"\nWrote fixtures for {len(results)} scenarios to demo/examples/.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
