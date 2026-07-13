# Failure Handling

How the Autonomous Lead Management Worker detects, retries, degrades, and escalates failures across five distinct failure domains.

---

## 1. Retry Strategy — Transient Tool Failures (Network / Timeouts)

**Library:** `tenacity` (configured per-tool wrapper)

**Location:** [`app/core/resilience.py`](../app/core/resilience.py) and individual tool files under [`app/agents/tools/`](../app/agents/tools/)

**Policy:**

| Tool category | Retry attempts | Backoff | On exhaustion |
|---|---|---|---|
| CRM update | 4 | Exponential: 2 s → 4 s → 8 s | Escalate to human |
| Enrichment API | 2 | Exponential: 1 s → 2 s | Graceful degrade (return `None` fields) |
| LLM call | 3 | Immediate (prompt-injection fallback) | Escalate to human |

**Key behaviour:** Decorators are applied directly on the inner callable; the LangGraph node function calls the `safe_*` wrapper which catches `tenacity.RetryError` and decides whether to raise (hard fail) or return a sentinel value (degrade).

---

## 2. Circuit Breakers — Protecting Downstream Systems

**Library:** `pybreaker`

**Location:** [`app/core/resilience.py`](../app/core/resilience.py)

| Breaker | `fail_max` | `reset_timeout` | Protects |
|---|---|---|---|
| `crm_breaker` | 5 consecutive failures | 60 s half-open | External CRM REST API |
| `db_breaker` | 3 consecutive failures | 30 s half-open | SQLAlchemy sync session |

When a breaker is **OPEN**, calls fail instantly with `CircuitBreakerError` — no network attempt is made. This prevents a cascading failure where thousands of inflight leads pile up connection attempts against a struggling downstream service.

The `safe_update_crm` wrapper catches `CircuitBreakerError` and routes the lead to the escalation queue rather than retrying through an open breaker.

---

## 3. LLM Malformed-JSON Fallback

**Location:** `generate_decision_with_llm` in [`app/core/resilience.py`](../app/core/resilience.py), invoked from the `analyze` node in [`app/agents/graph.py`](../app/agents/graph.py)

LLMs occasionally return syntactically invalid JSON. Simple retries reproduce the same hallucination. The strategy:

1. Catch `json.JSONDecodeError` on first LLM response.
2. Re-prompt the **same** LLM call, **injecting the exact parser error message** back into the prompt:
   > _"CRITICAL: Your previous response failed with error: `{e}`. FIX IT."_
3. Repeat up to 3 times total (via `@retry(stop=stop_after_attempt(3))`).
4. If all 3 attempts produce invalid JSON → `RetryError` → escalate node.

---

## 4. Graceful Degradation — Enrichment API Outage

**Location:** `safe_enrich_lead` in [`app/core/resilience.py`](../app/core/resilience.py), called from the `enrich` node.

The enrichment step (fetching company size / industry from an email domain) is **non-critical**. If the enrichment API is unreachable after 2 attempts:

- `safe_enrich_lead` catches `tenacity.RetryError` and returns:
  ```python
  {"company_name": None, "industry": None, "company_size": None}
  ```
- These `None` values are stored in `AgentState.memory`.
- The `analyze` node proceeds with partial data; the LLM prompt explicitly notes the missing enrichment context.
- The lead is still scored — it may receive a lower confidence score, potentially triggering the `LowConfidenceRule` → escalation.

The agent **never hard-stops** due to an enrichment outage.

---

## 5. Missing / Invalid Business Data — Escalation Policy

**Location:** `DecisionEngine` in [`app/agents/decision_engine.py`](../app/agents/decision_engine.py); escalation node in [`app/agents/graph.py`](../app/agents/graph.py)

Missing data is a **business logic** failure, not a system failure. The handling differs:

| Condition | Detected in | Outcome |
|---|---|---|
| `email` is `None` | `MissingEmailRule` (pre-LLM) | `halt_execution = True` → escalate |
| `phone` missing | `validate_lead_data()` in tools | Status → `ESCALATED` |
| LLM confidence < 0.70 | `LowConfidenceRule` | `halt_execution = True` → escalate |
| VIP / high-risk match | Future: `VIPMatchRule` | `halt_execution = True` → escalate |

When escalation is triggered:
1. `AgentState.status` is set to `WorkflowStatus.ESCALATED`.
2. The graph routes to the `escalate` node, which calls `notify` with an escalation payload.
3. Execution then converges on the `audit` node, which writes the final `ESCALATED` state to `workflow_states` and all collected `audit_logs` to `audit_logs`.
4. A human queue (Slack alert / CRM flag) receives the escalation. The workflow halts; the lead is not further modified by the agent.

---

## 6. Graph-Level Retry Routing

**Location:** `should_retry` conditional edge in [`app/agents/graph.py`](../app/agents/graph.py)

After the `analyze` node, the graph evaluates `AgentState.retry_count`:

- `retry_count < 3` → loop back to `analyze` (node increments counter before returning).
- `retry_count >= 3` → route to `escalate` unconditionally.

This prevents infinite retry loops on persistently bad LLM output.

---

## 7. Redis Unavailability — Checkpointer Fallback

**Location:** `get_checkpointer()` in [`app/core/memory.py`](../app/core/memory.py)

If the `REDIS_URL` environment variable is unset or Redis is unreachable at startup:

- `get_checkpointer()` returns `MemorySaver` (in-process dict-based checkpointer).
- The agent runs without crash-recovery capability; a pod restart will lose in-flight workflow state.
- A `WARNING` log is emitted at startup to make this trade-off visible.

This ensures the application starts cleanly in a local development environment without a Redis container.
