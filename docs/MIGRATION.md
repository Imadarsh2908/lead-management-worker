# Mapping this LangGraph workflow to an agent harness

> **Honesty note.** This document maps the project onto the *documented,
> public architecture* of harnesses such as **OpenClaw, NemoClaw, NanoClaw,
> and Hermes Agent** (and sandboxed runtimes like **OpenShell / NemoClaw**).
> It is a design mapping, **not** a verified integration — I have not executed
> against these harnesses' APIs. Where the analogy is loose I say so. Node,
> file, and function names cited here are the authoritative ones in
> [`app/agents/graph.py`](../app/agents/graph.py), which is the source of truth
> (the diagrams in [ARCHITECTURE.md](ARCHITECTURE.md) use simplified labels).

## a. Framing: model vs agent vs harness

A **model** is the LLM weights that turn a prompt into text (here: an
open-weight instruct model served via OpenRouter). An **agent** is the
model plus a control loop that lets it observe, decide, call tools, and loop
until a goal is met. A **harness** is the runtime that hosts the agent — it
owns the loop, the tool-execution sandbox, session/memory persistence, the
instruction-file format, and observability. This project is a **custom
harness**: LangGraph provides the orchestration loop (a `StateGraph` of nodes
and conditional edges), FastAPI provides the ingestion/observation surface,
Redis + PostgreSQL provide persistence (checkpointing + audit), and a set of
instruction files ([SOUL.md](../SOUL.md), [AGENTS.md](../AGENTS.md),
[TOOLS.md](../TOOLS.md), [config/policy.yaml](../config/policy.yaml)) externalize
behavior. Porting to OpenClaw/NemoClaw/Hermes means swapping *our* harness for
*theirs* while keeping the model, tools, and instruction files largely intact.

## b. Concept mapping

| This project | Harness concept | Notes |
|---|---|---|
| Graph nodes (`node_validate`, `node_enrichment`, `node_lead_score`, `node_generate_follow_up`, `node_notify`, `node_audit`, …) in `graph.py` | **Skills / agent steps** | Each node is a discrete, named unit of work with typed I/O — a clean 1:1 with a "skill" (OpenClaw) or a tool-backed step. Loose spot: our nodes are *deterministic Python*, not model-invoked; a harness may prefer the model to *choose* the next skill rather than a fixed graph. |
| Conditional edges (`route_after_validate`, `route_after_crm`, `route_after_enrichment`, `route_after_decision`, `route_after_retry`) | **Agent decision points / routing instructions** | Today these are pure functions over state (`confidence < gate → escalate`). In a harness they become either declarative routing rules or system-prompt policy the model follows. Loose spot: moving routing into the prompt trades determinism for flexibility — we would keep the guardrails deterministic (see sketch). |
| `AgentState` (Pydantic `BaseModel`, `app/agents/state.py`) | **Agent workspace / session memory** | Maps directly to a harness "session" object. Our `memory` dict ↔ scratch memory; `tool_history` ↔ the harness's tool-call log; typed fields (`priority`, `confidence`, `retry_count`, `enrichment_retry_count`) ↔ structured session variables. |
| Redis checkpointing (`get_checkpointer()` → `RedisSaver`, keyed by `workflow_id`) | **Persistent state & crash resume** | Snapshots state after every node. Harnesses that offer durable sessions cover this natively; ones that don't would need our checkpointer or an external store. Loose spot: exact-once resume semantics vary by harness. |
| [TOOLS.md](../TOOLS.md) contracts (`enrich_lead_domain`, `crm_lookup`, `send_notification`, `llm_score_lead`) | **Tool / skill definitions** | Our contract tables (purpose, input/output schema, timeout, retry, breaker, failure behavior) are exactly the metadata a harness tool manifest wants. Port with light reformatting into the harness's tool-spec format. |
| [AGENTS.md](../AGENTS.md) + [SOUL.md](../SOUL.md) + [config/policy.yaml](../config/policy.yaml) | **The harness's native instruction files** | AGENTS.md ↔ system/role prompt; SOUL.md ↔ guardrail/constitution file; policy.yaml ↔ tunable config the runtime reads. Several harnesses already read an `AGENTS.md`-style file, so this ports nearly as-is. |
| Audit trail (`audit_logs` → `AuditLog` table, written at the `audit` node; `/v1/leads/{id}/audit`) | **Harness observability / logging** | Our per-step records (action_type, tool I/O, reasoning, source, confidence) map to the harness's trace/event stream. Loose spot: harness traces are usually ephemeral/telemetry; our audit is a *durable compliance record* in Postgres — we would keep the DB sink even if the harness also traces. |

## c. Migration sketch

**Ports as-is (little to no change):**
- **Tools** — the four tools already have narrow, typed contracts and their own
  retry/breaker/degradation behavior. They become harness tool/skill definitions.
- **Instruction files** — SOUL.md, AGENTS.md, TOOLS.md, policy.yaml are already
  externalized and framework-agnostic. AGENTS.md is loaded at call time with
  mtime invalidation, so prompt edits need no restart — a property harnesses value.
- **Policy** — `policy.yaml` + `app/core/policy.py` (validated loader) is a
  drop-in config source; point the harness's config layer at it.

**Gets replaced:**
- **`StateGraph` orchestration → the harness's agent loop.** The nodes/edges in
  `graph.py` become skills + routing config. The retry loop
  (`route_after_retry`, `enrichment_retry_count`) and the confidence gate
  (`route_after_decision`) must be re-expressed as harness routing/guardrails.
- **FastAPI `BackgroundTasks` runner → the harness's execution/queue model.**
- **`RedisSaver` checkpointer → the harness's session persistence** (if durable),
  otherwise retained.

**Keep deterministic even after migration:** the guardrails in SOUL.md
(confidence gate, escalate-on-missing-contact, degrade-don't-fabricate, bounded
retries) should stay as *code/config the runtime enforces*, not prompt text the
model may ignore. The rule engine ([`decision_engine.py`](../app/agents/decision_engine.py))
should remain a post-model guardrail that can only make the outcome stricter.

**What a sandboxed runtime (NemoClaw / OpenShell) adds — and why it matters here:**
This system processes **micro-entrepreneur PII** (names, emails, company data,
budgets) and calls **money-adjacent external systems** (CRM, notifications). A
sandboxed runtime contributes:
- **Network policy / egress allow-listing** — the agent can only reach the CRM,
  enrichment, and LLM endpoints, not arbitrary hosts. Contains a prompt-injected
  or buggy tool from exfiltrating lead data.
- **Credential isolation** — API keys (OpenRouter, CRM) are injected by the
  runtime per tool call, never visible to the model context. Today keys live in
  `settings`/env; a sandbox scopes them per tool.
- **Audited tool execution** — the runtime records every tool invocation with
  inputs/outputs at the boundary, complementing our in-app audit trail with an
  independent, tamper-resistant log. For a business handling other people's
  customer data, that dual record is the difference between "we think it behaved"
  and "we can prove what it did."

## d. Effort estimate

| Component | Effort (S/M/L) | Risk | Notes |
|---|---|---|---|
| Tool definitions (`TOOLS.md` → harness tool manifest) | **S** | Low | Contracts already exist; mechanical reformat. |
| Instruction files (SOUL/AGENTS/policy) | **S** | Low | AGENTS.md-style already assumed by some harnesses. |
| Re-express routing/guardrails (edges → harness routing) | **M** | Med | Must preserve determinism of the confidence gate + retry ceiling; risk is the model "routing" past a guardrail. |
| Orchestration loop (`StateGraph` → harness loop) | **M–L** | Med | Core rewrite; behavior parity must be re-verified against `demo/examples/`. |
| Session persistence / crash resume | **M** | Med | Depends on whether the harness offers durable sessions; may keep `RedisSaver`. |
| Audit sink (durable Postgres record) | **S–M** | Low | Keep the DB writer; add a hook from the harness trace stream. |
| Sandbox integration (egress policy, credential injection) | **M** | Med–High | New surface; highest security value, needs careful allow-list + secret scoping. |
| Regression parity (re-run the 7 demo scenarios on the new harness) | **M** | Med | `demo/examples/*` are the golden outputs to diff against. |
