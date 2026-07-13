# Design Decisions

Key engineering choices made in the Autonomous Lead Management Worker, with rationale and alternatives considered.

---

## 1. Pydantic `BaseModel` for Agent State (vs `TypedDict`)

**Decision:** [`app/agents/state.py`](../app/agents/state.py) defines `AgentState` as a Pydantic `BaseModel`, not LangGraph's conventional `TypedDict`.

**Why:**
- Provides runtime field-level validation at state boundaries — a critical safety property for an autonomous agent whose mutations come from LLM tool-call output.
- Enables `model_dump()` / `model_validate()` for clean Redis serialisation without custom codecs.
- Field defaults (e.g. `retry_count: int = 0`) remove a class of `KeyError` bugs that `TypedDict` cannot prevent.

**Trade-off:** Pydantic validation adds ~5–10 µs overhead per node transition. Irrelevant vs. the hundreds of milliseconds spent on LLM API calls.

**Alternative rejected:** A plain `TypedDict` would be lighter and is the LangGraph idiomatic choice, but sacrifices validation — unacceptable for a system that must never silently corrupt a lead record.

---

## 2. Redis Checkpointing for Mid-Run Crash Recovery

**Decision:** The compiled `StateGraph` is wrapped with a `RedisSaver` checkpointer ([`app/core/memory.py`](../app/core/memory.py)).

**Why:**
- FastAPI's `BackgroundTasks` are in-process — a pod OOM kill silently drops the workflow.
- Redis checkpointing persists `AgentState` after **every node** completes. On restart, `app.invoke(None, config={"configurable": {"thread_id": workflow_id}})` resumes exactly where execution stopped without re-executing prior nodes.

**Trade-off:** Adds a Redis dependency; the app gracefully falls back to `MemorySaver` (in-memory, no crash recovery) when `REDIS_URL` is unset (local dev).

**Known limitation (staff review):** A more robust production design would push leads to a Kafka topic so a dedicated worker process — not a FastAPI background task — drives execution. This eliminates the in-process queue entirely. Tracked as a future improvement.

---

## 3. Rule Engine as a Pre-LLM Guardrail Layer

**Decision:** [`app/agents/decision_engine.py`](../app/agents/decision_engine.py) runs a deterministic rule engine **before** the LLM classify step, not after.

**Design:** Each business rule is a separate `BaseRule` subclass (Open/Closed Principle). The engine iterates the ordered list and short-circuits on `halt_execution = True`.

| Rule | Trigger | Effect |
|---|---|---|
| `MissingEmailRule` | `email` is `None` | Halt → escalate |
| `LowConfidenceRule` | LLM confidence < 0.70 | Halt → escalate |
| `HighBudgetRule` | `budget` > 500,000 | Priority → HIGH |
| `DecisionMakerRoutingRule` | job title contains CEO/VP/etc. | Queue → SENIOR_SALES |

**Why run deterministic rules first:** This prevents wasting LLM tokens on leads that will always escalate (e.g. no email). It also creates a predictable, auditable decision path that is not sensitive to LLM non-determinism.

**Extending:** Add a new rule class to the list in `DecisionEngine.__init__`; no other code changes needed.

---

## 4. The `audit` Convergence Node

**Decision:** Both the happy path and escalation path converge on a single terminal `audit` node before the graph ends.

**Why:**
- Guarantees that **every** workflow execution — regardless of outcome — writes its `AuditLog` records and updates `WorkflowState` in PostgreSQL exactly once.
- Avoids duplicating DB commit logic across multiple terminal nodes.
- Makes it trivially easy to add post-processing (e.g. analytics events) in one place.

**Implementation:** See `node_audit` in [`app/agents/graph.py`](../app/agents/graph.py). It bulk-inserts `state.audit_logs` accumulated during the run, then sets `current_status` to the final `state.status`.

---

## 5. Soft-Delete for Leads

**Decision:** `DELETE /leads/{id}` sets `is_deleted = True` rather than removing the row.

**Why:** Audit logs reference `lead_id` as a foreign key. Hard-deleting a lead would orphan its entire audit trail, destroying the compliance record.

**Access control:** Only the `Admin` role can call the delete endpoint. The `GET /leads/` query always filters `is_deleted = False`.

---

## 6. HS256 JWT with Role Claims

**Decision:** Tokens are signed with HMAC-SHA256 using a single `SECRET_KEY`. Role (`Admin` / `Sales` / `Operator`) is embedded as a JWT claim and checked via `get_current_user` dependency.

**Trade-off acknowledged (staff review):**
- Stateless tokens cannot be revoked before expiry — a stolen refresh token is valid for 7 days.
- HS256 requires sharing the secret key; RS256/JWKS would be preferable for multi-service deployments.

**Planned fix:** Redis-backed token revocation list on the `/auth/refresh` endpoint.
