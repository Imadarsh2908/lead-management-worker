#!/usr/bin/env python
"""
demo/run_demo.py
----------------
Demo harness for the Autonomous Lead Management Worker.

    python demo/run_demo.py <scenario> [--base-url URL] [--timeout S] [--capture]

For the chosen scenario it:
  (a) prints what it demonstrates and which requirement it maps to,
  (b) logs in via POST /v1/auth/login with the demo credentials,
  (c) POSTs the scenario's lead payload,
  (d) polls GET /v1/leads/{id}/status until COMPLETED or ESCALATED (timeout),
  (e) fetches GET /v1/leads/{id}/audit and pretty-prints a compact timeline.

Exit code is non-zero if the observed final status does not match the
scenario's expectation — so this doubles as a smoke test.

The harness is a pure HTTP client; it talks to a running server (see
demo/.env.demo + README "Running the demo"). It never mutates your .env and
never sets the SERVER's environment — each scenario prints the server env it
requires.
"""
import argparse
import json
import os
import sys
import time
import uuid
from typing import Optional, Tuple

import requests

# Make stdout robust on Windows consoles (cp1252) — audit data may contain
# non-ASCII (e.g. the ₹ used in rule reasoning). Never let printing crash a run.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# Allow running as `python demo/run_demo.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from demo.scenarios import SCENARIOS, scenario_names  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("DEMO_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_USER = os.environ.get("DEMO_USER", "admin_user")
DEFAULT_PASSWORD = os.environ.get("DEMO_PASSWORD", "password123")
EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")

# Exit codes
EXIT_OK = 0
EXIT_MISMATCH = 1     # ran, but final status != expectation
EXIT_SETUP = 2        # couldn't reach server / login / submit


# ─────────────────────────────────────────────────────────────
# Pretty printing
# ─────────────────────────────────────────────────────────────

def hr(char="-", width=72):
    print(char * width)


def banner(scenario_key: str, sc: dict):
    hr("=")
    print(f"  SCENARIO: {scenario_key}")
    print(f"  MAPS TO : {sc['requirement']}")
    hr("=")
    # Wrap the explanation to ~72 cols for readable terminal output.
    import textwrap
    for line in textwrap.wrap(sc["explanation"], width=72):
        print("  " + line)
    env = sc.get("server_env") or {}
    if env:
        print()
        print("  Server must be running with:")
        for k, v in env.items():
            print(f"    {k}={v}")
    hr()


def _coalesce_source_confidence(entry: dict) -> Tuple[Optional[str], Optional[float]]:
    """Extract source/confidence from an audit row, wherever they were recorded."""
    outputs = entry.get("tool_outputs") or {}
    reasoning = entry.get("llm_reasoning") or {}
    source = outputs.get("source")
    confidence = outputs.get("confidence")
    if confidence is None:
        confidence = reasoning.get("confidence")
    return source, confidence


def _row_message(entry: dict) -> str:
    """
    The human-readable message. For reasoning/state rows node_audit nests it under
    llm_reasoning.message (the top-level column is only set for tool rows), so fall
    back to that.
    """
    msg = entry.get("message")
    if not msg:
        msg = (entry.get("llm_reasoning") or {}).get("message")
    return (msg or "").strip()


def print_timeline(audit: list):
    print("  AUDIT TIMELINE")
    hr()
    if not audit:
        print("  (no audit rows)")
        hr()
        return
    for i, entry in enumerate(audit, start=1):
        action = entry.get("action_type", "?")
        message = _row_message(entry)
        source, confidence = _coalesce_source_confidence(entry)
        extra = []
        if source:
            extra.append(f"source={source}")
        if confidence is not None:
            try:
                extra.append(f"conf={float(confidence):.2f}")
            except (TypeError, ValueError):
                extra.append(f"conf={confidence}")
        suffix = f"  ({', '.join(extra)})" if extra else ""
        # Trim long messages so the timeline stays compact.
        if len(message) > 88:
            message = message[:85] + "..."
        print(f"  #{i:02d} [{action}] {message}{suffix}")
    hr()


# ─────────────────────────────────────────────────────────────
# HTTP steps
# ─────────────────────────────────────────────────────────────

def login(base_url: str, username: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/v1/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def submit_lead(base_url: str, token: str, sc: dict, payload: dict) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"}
    if sc["endpoint"] == "raw":
        headers["X-Demo-Raw"] = "true"
        url = f"{base_url}/v1/leads/raw"
    else:
        url = f"{base_url}/v1/leads/"
    return requests.post(url, json=payload, headers=headers, timeout=15)


def poll_status(base_url: str, token: str, lead_id: str, timeout_s: int) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout_s
    last = "UNKNOWN"
    while time.time() < deadline:
        resp = requests.get(f"{base_url}/v1/leads/{lead_id}/status", headers=headers, timeout=10)
        if resp.status_code == 200:
            last = resp.json().get("status", "UNKNOWN")
            print(f"    ...status={last}")
            if last in ("COMPLETED", "ESCALATED", "FAILED"):
                return last
        time.sleep(1.0)
    print(f"    ...timed out after {timeout_s}s (last status: {last})")
    return last


def fetch_audit(base_url: str, token: str, lead_id: str) -> list:
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{base_url}/v1/leads/{lead_id}/audit", headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────
# Scenario runner
# ─────────────────────────────────────────────────────────────

def _prepare_payload(sc: dict) -> dict:
    """Copy the payload and make the email unique per run when required."""
    payload = dict(sc["payload"])
    if sc.get("unique_email") and payload.get("email"):
        local, _, domain = payload["email"].partition("@")
        payload["email"] = f"{local}+{uuid.uuid4().hex[:8]}@{domain}"
    return payload


def run_scenario(scenario_key: str, base_url: str, timeout_s: int,
                 username: str, password: str, capture: bool) -> int:
    sc = SCENARIOS[scenario_key]
    banner(scenario_key, sc)

    # (b) login
    print("  [1] Logging in via /v1/auth/login ...")
    try:
        token = login(base_url, username, password)
    except requests.exceptions.ConnectionError:
        print(f"\n  [FAIL] Could not reach the server at {base_url}.")
        print("    Start it first (offline, no Postgres/Redis needed):")
        print("      bash demo/serve_demo.sh          # or see README 'Running the demo'")
        return EXIT_SETUP
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] Login failed: {e}")
        return EXIT_SETUP
    print("      [OK] authenticated")

    # (c) submit
    payload = _prepare_payload(sc)
    print(f"  [2] Submitting lead -> {'POST /v1/leads/raw (demo seam)' if sc['endpoint']=='raw' else 'POST /v1/leads/'}")
    resp = submit_lead(base_url, token, sc, payload)

    # duplicate_lead: the headline is the 409 on the SECOND submit.
    if sc.get("expect_conflict_on_resubmit"):
        if resp.status_code != 202:
            print(f"  [FAIL] First submit expected 202, got {resp.status_code}: {resp.text}")
            return EXIT_SETUP
        first_id = resp.json()["id"]
        print(f"      [OK] first submit accepted (202), lead {first_id}")
        print("  [2b] Resubmitting the SAME email ...")
        dup = submit_lead(base_url, token, sc, payload)
        print(f"      -> HTTP {dup.status_code} ({'409 Conflict — dedup' if dup.status_code == 409 else 'unexpected'})")
        ok = dup.status_code == 409
        # Let the first lead finish so we can show its audit trail too.
        final = poll_status(base_url, token, first_id, timeout_s)
        audit = fetch_audit(base_url, token, first_id)
        print_timeline(audit)
        print(f"  RESULT: resubmit returned {dup.status_code} "
              f"(expected 409); first lead final status = {final}")
        _maybe_capture(capture, scenario_key, payload, final, audit, extra={"resubmit_http": dup.status_code})
        if ok:
            print("  [OK] PASS — duplicate correctly rejected with 409 (API-level dedup).")
            return EXIT_OK
        print("  [FAIL] FAIL — expected a 409 on resubmit.")
        return EXIT_MISMATCH

    if resp.status_code != sc["expect_http"]:
        print(f"  [FAIL] Submit expected HTTP {sc['expect_http']}, got {resp.status_code}: {resp.text}")
        return EXIT_SETUP
    lead_id = resp.json()["id"]
    print(f"      [OK] accepted (HTTP {resp.status_code}), lead {lead_id}")

    # (d) poll
    print(f"  [3] Polling status (timeout {timeout_s}s) ...")
    final = poll_status(base_url, token, lead_id, timeout_s)

    # (e) audit timeline
    print("  [4] Fetching audit trail ...")
    audit = fetch_audit(base_url, token, lead_id)
    print_timeline(audit)

    # Assertions
    audit_blob = json.dumps(audit).lower()
    missing = [s for s in sc.get("audit_contains", []) if s.lower() not in audit_blob]

    print(f"  RESULT: final status = {final} (expected {sc['expect_status']})")
    _maybe_capture(capture, scenario_key, payload, final, audit)

    if final != sc["expect_status"]:
        print(f"  [FAIL] FAIL — status mismatch. If this ran against a live server, confirm it "
              f"was started with the env shown above.")
        return EXIT_MISMATCH
    if missing:
        print(f"  [FAIL] FAIL — expected audit markers not found: {missing}")
        return EXIT_MISMATCH
    print("  [OK] PASS")
    return EXIT_OK


def _trim_audit(audit: list) -> list:
    """Trim audit rows to the key fields for the example fixtures."""
    trimmed = []
    for e in audit:
        source, confidence = _coalesce_source_confidence(e)
        row = {"action_type": e.get("action_type"), "message": _row_message(e) or None}
        if source is not None:
            row["source"] = source
        if confidence is not None:
            row["confidence"] = confidence
        trimmed.append(row)
    return trimmed


def _maybe_capture(capture: bool, scenario_key: str, payload: dict,
                   final: str, audit: list, extra: dict = None):
    if not capture:
        return
    d = os.path.join(EXAMPLES_DIR, scenario_key)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "input.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    out = {"scenario": scenario_key, "final_status": final, "audit": _trim_audit(audit)}
    if extra:
        out.update(extra)
    with open(os.path.join(d, "expected_output.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"      [OK] captured examples/{scenario_key}/input.json + expected_output.json")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Demo harness for the Autonomous Lead Management Worker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Scenarios:\n  " + "\n  ".join(
            f"{k:<16} {SCENARIOS[k]['requirement']}" for k in scenario_names()
        ),
    )
    parser.add_argument("scenario", choices=scenario_names(), help="Which scenario to run.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default {DEFAULT_BASE_URL})")
    parser.add_argument("--timeout", type=int, default=30, help="Poll timeout in seconds (default 30)")
    parser.add_argument("--username", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--capture", action="store_true",
                        help="Write demo/examples/<scenario>/{input,expected_output}.json from this run.")
    args = parser.parse_args()

    return run_scenario(args.scenario, args.base_url, args.timeout,
                        args.username, args.password, args.capture)


if __name__ == "__main__":
    sys.exit(main())
