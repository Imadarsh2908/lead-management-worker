# DEMO_SCRIPT.md — 5–7 minute walkthrough

Shot-by-shot script for the demo video. The failure scenarios are the heart of
the video — spend the most time there. Every shot lists the exact command.

**One-time setup (before recording):**
```bash
python -m venv venv && source venv/Scripts/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```
All commands run from the repo root. The harness talks to a running server over
HTTP; each failure scenario needs the server started with specific env (shown
per shot). Two terminals: **[SERVER]** (left) and **[DEMO]** (right).

---

## Shot 1 — Architecture (0:00–0:30, 30s)

**On screen:** `docs/ARCHITECTURE.md` mermaid diagram + `SOUL.md`.

**Say:** "An autonomous worker that takes a raw lead and drives it to a decision
— validate, dedupe, enrich, score with an LLM, then a deterministic rule
guardrail that can only make the outcome *stricter*. Every run is fully audited.
Six inviolable guardrails live in SOUL.md; the numbers behind them live in
config/policy.yaml."

**Show the graph:**
```
receive_lead → validate → crm_lookup → enrichment → lead_score → decision
          ↘ escalate ↗          ↘(retry loop)↗        (LLM + guardrail)   ↘ generate_follow_up → notify → audit
                                                                          ↘ notify / escalate ↗
```

---

## Shot 2 — Happy path (0:30–2:00, 90s)

**[SERVER]** start with a real model configured:
```bash
LLM_ENABLED=true LLM_API_KEY=sk-or-... LLM_MODEL=qwen/qwen-2.5-7b-instruct \
  bash demo/serve_demo.sh
```
**[DEMO]**
```bash
python demo/run_demo.py happy_path
```
**Say:** "A complete enterprise lead — €750k budget, a VP. Watch it flow end to
end: validate, CRM lookup, enrichment, the LLM scores it HIGH with high
confidence, the rule guardrail agrees, and the worker autonomously drafts a
follow-up. Final status COMPLETED, and the whole reasoning chain is in the audit
timeline — `source=llm`, the confidence, every tool call." Point at the
`generate_follow_up` and `send_slack_notification` rows and the exit code 0.

---

## Shot 3 — FAILURE SCENARIOS (2:00–5:30, ~3.5 min) ★ the main event ★

> Each scenario = restart **[SERVER]** with the shown env, then one **[DEMO]**
> command. Narrate the guardrail each one proves.

### 3a — Missing contact info → escalate (~35s)
Server: default offline server is fine (`bash demo/serve_demo.sh`).
```bash
python demo/run_demo.py missing_email
```
**Say:** "No email. The strict API would reject it, so we use the env-guarded
demo seam `/v1/leads/raw`. The validate node refuses to guess who to contact and
escalates. ESCALATED — guardrail #2."

### 3b — Enrichment down → degrade, don't fabricate (~45s)
**[SERVER]**
```bash
ENRICHMENT_API_URL=http://127.0.0.1:9 LLM_ENABLED=true LLM_API_KEY=sk-or-... \
  bash demo/serve_demo.sh
```
**[DEMO]**
```bash
python demo/run_demo.py enrichment_down
```
**Say:** "Enrichment API is dead (port 9 refuses). It retries to the configured
ceiling, then *proceeds with `enrichment_failed=True`* — it never invents a
company. Still COMPLETED, degraded honestly. Guardrail #3."

### 3c — Malformed LLM JSON → self-correct (~40s)
**[SERVER]**
```bash
LLM_FORCE_MALFORMED=true LLM_ENABLED=true LLM_API_KEY=sk-or-... bash demo/serve_demo.sh
```
**[DEMO]**
```bash
python demo/run_demo.py llm_malformed
```
**Say:** "First model response is broken JSON. The worker re-prompts once with
the exact parse error; the retry parses. Audit shows
`source=llm_selfcorrected`."

### 3d — LLM dead → rules fallback → escalate (~40s)  *(runs fully offline)*
**[SERVER]**
```bash
LLM_ENABLED=false bash demo/serve_demo.sh
```
**[DEMO]**
```bash
python demo/run_demo.py llm_dead
```
**Say:** "Total model outage. Fall back to the rule engine, stamp confidence
0.50 — below the gate — so it escalates rather than acting blind.
`source=rules_fallback`, ESCALATED. Guardrail #1 + #5."

### 3e — Low confidence → escalate (~35s)
**[SERVER]**
```bash
LLM_FORCE_CONFIDENCE=0.40 LLM_ENABLED=true LLM_API_KEY=sk-or-... bash demo/serve_demo.sh
```
**[DEMO]**
```bash
python demo/run_demo.py low_confidence
```
**Say:** "An ambiguous lead. The model is unsure (pinned to 0.40 via the
demo-only, env-guarded knob so it's reproducible). Below the gate → human.
Guardrail #6 — the confidence gate."

### 3f — Duplicate lead → 409 (~25s)  *(runs fully offline)*
```bash
python demo/run_demo.py duplicate_lead
```
**Say:** "Same email twice. The API dedupes at ingestion — 409 Conflict before
the workflow even starts. Idempotent ingestion, guardrail #7."

---

## Shot 4 — What v2 improves (5:30–6:00, 30s)

**Say:** "Next: swap the in-memory checkpointer for Redis so a crash resumes
mid-workflow; move demo auth to real DB-backed users; add a DLQ + replay for the
CRM circuit-breaker-open path; stream the audit timeline to a live dashboard; and
expand AGENTS.md with few-shot calibration to raise autonomous-action rates
without touching the guardrails."

---

## Notes for the recorder
- Fully offline shots (no LLM key needed): **llm_dead, duplicate_lead, missing_email**.
- Shots needing an OpenRouter key: **happy_path, enrichment_down, llm_malformed, low_confidence**.
- Golden outputs for every scenario are checked in under `demo/examples/<scenario>/`.
- Regenerate them any time with `python demo/capture_examples.py` (offline).
- Exit code is 0 on the expected outcome, non-zero otherwise — the harness is
  also a smoke test.
