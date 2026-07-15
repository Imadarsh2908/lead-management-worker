# Roadmap

## What v1 does autonomously

Each capability below ships in v1 and is provable with a one-line demo command
(see [demo/DEMO_SCRIPT.md](../demo/DEMO_SCRIPT.md) for the full walkthrough and
which scenarios run fully offline).

- **Ingests, validates, enriches, scores, and routes a lead end-to-end** with no
  human in the loop for the common case — draft follow-up and notify on a HIGH
  lead. → `python demo/run_demo.py happy_path`
- **Refuses to act on missing contact info** — the validate node escalates rather
  than guessing who to contact. → `python demo/run_demo.py missing_email`
- **Degrades instead of fabricating** when enrichment is down — retries to the
  configured ceiling, then proceeds with `enrichment_failed=True` rather than
  inventing company data. → `python demo/run_demo.py enrichment_down`
- **Self-corrects malformed LLM output** — re-prompts once with the exact parse
  error; audit shows `source=llm_selfcorrected`. → `python demo/run_demo.py llm_malformed`
- **Survives a total model outage** — falls back to the deterministic rule engine
  at confidence 0.50 (below the gate) and escalates by design. → `python demo/run_demo.py llm_dead`
- **Escalates low-confidence decisions** through the confidence gate instead of
  acting on a weak signal. → `python demo/run_demo.py low_confidence`
- **Deduplicates ingestion** — a repeated email is rejected with HTTP 409 before
  the workflow starts. → `python demo/run_demo.py duplicate_lead`
- **Persists a full, queryable audit trail** for every run (state transitions,
  tool I/O, reasoning, guardrail overrides). → `GET /v1/leads/{id}/audit`
  (rendered by any scenario's timeline).

## What v2 improves

- **Real CRM + Slack integrations behind the existing tool contracts.** The
  [TOOLS.md](../TOOLS.md) contracts for `crm_lookup` and `send_notification` are
  already defined; v2 implements the live adapters without changing callers or
  the audit shape — the contract is the seam.
- **Task queue (RQ / Celery) replacing FastAPI `BackgroundTasks`.** Rationale:
  (1) *worker blocking* — background tasks share the web worker's event loop/
  process, so a slow LLM call steals capacity from request handling; (2) *retry
  ownership* — a real broker owns ret/ack/dead-letter semantics instead of our
  in-process loop; (3) *horizontal scale* — queue workers scale independently of
  the API tier. This also closes the known limitation in
  [DECISIONS.md §2](DECISIONS.md).
- **Async graph execution.** Move node tool calls to async I/O so a single worker
  handles many in-flight leads concurrently instead of one-blocking-thread-each.
- **LLM follow-up drafting with a human-approval gate.** Let the model draft the
  outbound message, but require human approval before send for HIGH-value or
  low-confidence leads — autonomy with a safety interlock.
- **A small eval set + harness for the scoring prompt.** A labeled fixture set
  that measures **priority accuracy** and **escalation precision/recall** so that
  edits to [AGENTS.md](../AGENTS.md) can be scored before shipping — turning
  prompt changes from vibes into a measured regression gate.
- **Escalated-lead replay after human correction.** When a human fixes the reason
  a lead escalated (adds the missing email, overrides a priority), replay the
  workflow from that point using the existing checkpoint rather than restarting —
  built on the Redis checkpointer already in place.
