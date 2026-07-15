# Submission Checklist

Every assignment deliverable mapped to the exact file path or demo command that
satisfies it. Items that are **not yet backed by a real artifact are flagged
`TODO` / `ACTION`** rather than papered over.

| # | Deliverable | Satisfied by | Status |
|---|---|---|---|
| 1 | **GitHub repository** | This repository (root `README.md` is the entry point). | ⚠️ **ACTION** — no git remote is configured. Run `git remote add origin <url>` and `git push -u origin main` before submitting. |
| 2 | **Demo video** | Shot-by-shot script + exact commands: [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md). | ⚠️ **TODO** — the script exists; the recorded video is **not** in the repo. Record it and add the link here. |
| 3 | **Workflow spec files** | Graph definition [`app/agents/graph.py`](app/agents/graph.py); diagram in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); behavior spec [SOUL.md](SOUL.md); scoring prompt [AGENTS.md](AGENTS.md); tunable policy [config/policy.yaml](config/policy.yaml). | ✅ Done |
| 4 | **Input / output examples** | `demo/examples/<scenario>/input.json` + `expected_output.json` for all 7 scenarios (regenerate with `python demo/capture_examples.py`), PLUS 18 boundary/edge-case input-output pairs in `demo/edge_cases/` — see [demo/edge_cases/README.md](demo/edge_cases/README.md) (regenerate with `python demo/capture_edge_cases.py`). 6 of the 18 are flagged `known_issue: true` — verified current behavior worth a second look (see row 13a). | ✅ Done |
| 5 | **Tool definitions & contracts** | [TOOLS.md](TOOLS.md) (contract tables) + implementations in [`app/agents/tools/`](app/agents/tools/) and [`app/core/resilience.py`](app/core/resilience.py). | ✅ Done |
| 6 | **Workflow states & transitions** | Nodes + conditional edges in [`app/agents/graph.py`](app/agents/graph.py); `WorkflowStatus` enum in [`app/models/lead.py`](app/models/lead.py); mermaid flow in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). | ✅ Done (diagram uses simplified labels; `graph.py` is authoritative). |
| 7 | **Memory / state strategy** | `AgentState` in [`app/agents/state.py`](app/agents/state.py); Redis checkpointing in [`app/core/memory.py`](app/core/memory.py); rationale in [docs/DECISIONS.md](docs/DECISIONS.md) §1–§2 and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) "Agent State Model". | ✅ Done |
| 8 | **Exception handling & retry logic** | [docs/FAILURE_HANDLING.md](docs/FAILURE_HANDLING.md) §1–§4, §6–§7; implementation in [`app/core/resilience.py`](app/core/resilience.py) (Tenacity retries + PyBreaker). Proven: `python demo/run_demo.py enrichment_down` and `python demo/run_demo.py llm_malformed`. | ✅ Done |
| 9 | **Escalation policy** | Guardrails in [SOUL.md](SOUL.md); [docs/FAILURE_HANDLING.md](docs/FAILURE_HANDLING.md) §5; [`app/agents/decision_engine.py`](app/agents/decision_engine.py) + `route_after_validate` / `route_after_decision` in `graph.py`. Proven: `python demo/run_demo.py missing_email`, `llm_dead`, `low_confidence`. | ✅ Done |
| 10 | **Audit & logging strategy** | `node_audit` in [`app/agents/graph.py`](app/agents/graph.py) → `AuditLog` table in [`app/models/lead.py`](app/models/lead.py); structured logging in [`app/core/logging_config.py`](app/core/logging_config.py); [docs/DECISIONS.md](docs/DECISIONS.md) §4. Inspect via `GET /v1/leads/{id}/audit` (any scenario prints the timeline). | ✅ Done |
| 11 | **Current autonomy (what v1 does)** | [docs/ROADMAP.md](docs/ROADMAP.md) "What v1 does autonomously" — each bullet ends with its proving demo command. | ✅ Done |
| 12 | **Next version (what v2 improves)** | [docs/ROADMAP.md](docs/ROADMAP.md) "What v2 improves". | ✅ Done |
| 13 | **Intentional failure demo** | Failure scenarios `missing_email`, `enrichment_down`, `llm_malformed`, `llm_dead`, `low_confidence` — commands in [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md) §3; golden outputs in `demo/examples/`. | ✅ Done |
| 13a | **Edge-case & boundary coverage** | [demo/edge_cases/README.md](demo/edge_cases/README.md) — 18 verified input/output pairs: threshold/gate boundaries (budget, confidence), field-length limits, and 6 items flagged `known_issue: true` (see below). Locked in as regression tests in [tests/test_edge_cases.py](tests/test_edge_cases.py) (132/132 suite passing). | ✅ Done |

## Known issues discovered while capturing edge-case data

Found by actually running boundary inputs through the real code (not guessed) — see
[demo/edge_cases/README.md](demo/edge_cases/README.md) for full detail on each:

1. **Title-keyword matching is bare-substring, not word-boundary-aware** — `DecisionMakerRoutingRule`
   false-positives on "Coordinator" (contains "coo") and "Contractor" (contains "cto"), and conversely
   false-negatives a real "Chief Executive Officer" (no "ceo" substring when spelled out in full).
2. **SPAM priority is unreachable via the rules engine** — only a genuine LLM classification can produce it;
   the deterministic fallback can only ever return HIGH/MEDIUM/LOW.
3. **Case-variant duplicate email causes an unhandled HTTP 500** instead of the intended 409 — the
   ingestion dedup check compares the raw request email (case-sensitive) against the stored, lowercased
   value, so a same-address-different-case resubmission slips past the pre-check and hits the DB's UNIQUE
   constraint on write.

None of these are fixed yet — they're captured as a regression baseline (`tests/test_edge_cases.py`,
prefixed `test_KNOWN_ISSUE_*`) so a deliberate fix updates an assertion, rather than silently drifting.

## Open items before submitting

- **ACTION (#1):** configure the GitHub remote and push.
- **TODO (#2):** record the demo video following [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md) and paste the link into row 2.
- **Optional:** decide whether to fix the 3 known issues above before submitting, or note them as "v1 gaps, addressed in v2" in [docs/ROADMAP.md](docs/ROADMAP.md).

## One-command verification

Fully offline, no external services or API keys required:

```bash
pip install -r requirements.txt
pytest -q                                        # full test suite
bash demo/serve_demo.sh &                         # offline SQLite server
python demo/run_demo.py llm_dead                  # end-to-end failure demo (exit 0 = pass)
python demo/run_demo.py duplicate_lead            # dedup demo (exit 0 = pass)
python demo/capture_examples.py                   # regenerate all I/O example fixtures
python demo/capture_edge_cases.py                 # regenerate boundary/edge-case fixtures
```
