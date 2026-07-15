# TOOLS.md — Tool Contracts

Every external capability the worker uses, as a contract. Numeric values are NOT
duplicated here — they come from `config/policy.yaml` (referenced symbolically).
See `SOUL.md` for the principles (degrade-don't-fabricate, bounded retries,
escalate-on-missing-info) these contracts implement.

Legend for **Failure behavior**:
- **Degrade** — return partial/empty data flagged as missing; workflow continues.
- **Escalate** — hand the lead to a human.
- **DLQ** — route to a dead-letter queue for later replay.

---

## `enrich_lead_domain`

| Field | Contract |
|---|---|
| **Purpose** | Extract the domain from a lead's email and fetch company context (name, industry, size). Freemail domains short-circuit (no API call). |
| **Input** | `{ email: str (email format) }` |
| **Output** | `{ domain: str, company_name: str?, industry: str?, company_size: str?, is_freemail: bool, enrichment_failed: bool }` |
| **Timeout** | `policy.resilience.enrichment_timeout_seconds` per request |
| **Retry policy** | Exponential backoff, network errors only (`Timeout`/`ConnectionError`); attempts bounded by tenacity `stop_after_attempt` |
| **Circuit breaker** | `enrichment_breaker` — `policy.resilience.enrichment_breaker.{fail_max, reset_timeout}` |
| **Failure behavior** | **Degrade** — returns `enrichment_failed=true` with null company fields; the graph retries up to `policy.workflow.max_retries` then proceeds with degraded data. Never fabricates company data. Never escalates on enrichment alone. |

## `crm_lookup`

| Field | Contract |
|---|---|
| **Purpose** | Check whether a lead already exists in the CRM (dedup), by email, excluding the current lead. |
| **Input** | `{ email: str, lead_id: uuid }` (from graph state) |
| **Output** | `{ exists_in_crm: bool }` (written into `state.memory`) |
| **Timeout** | DB session (CRM write path uses `policy.resilience.crm_timeout_seconds`) |
| **Retry policy** | No application-level retry on lookup (single query); CRM *writes* retry with exponential backoff (network errors only) |
| **Circuit breaker** | `crm_breaker` — `policy.resilience.crm_breaker.{fail_max, reset_timeout}` (guards CRM writes) |
| **Failure behavior** | **Degrade** — on DB error, assume a new lead (`exists_in_crm=false`) and record a warning audit entry; workflow continues. CRM-write breaker open → **DLQ**. |

## `send_notification`

| Field | Contract |
|---|---|
| **Purpose** | Notify the sales team (Slack/email) with the lead summary and any drafted follow-up. |
| **Input** | `{ lead_id, priority, email, company, draft_email? }` |
| **Output** | `{ status: "sent" }` |
| **Timeout** | Delivery channel default (webhook/email client) |
| **Retry policy** | None at the graph layer (notification is best-effort, terminal-ish step) |
| **Circuit breaker** | None (no shared downstream to protect) |
| **Failure behavior** | **Degrade** — a failed notification is logged; it does not roll back the workflow. |

## `llm_score_lead`

| Field | Contract |
|---|---|
| **Purpose** | Score a lead with the LLM (priority + confidence + reasoning). System prompt from `AGENTS.md`; output validated to a strict JSON schema. |
| **Input** | `{ context: {email, budget, job_title, company, company_size, is_freemail, enrichment_failed} }` |
| **Output** | `{ priority, confidence (0..1), next_action, reasoning[], source }` where `source ∈ {llm, llm_selfcorrected, rules_fallback}` |
| **Timeout** | `settings.LLM_TIMEOUT_SECONDS` per request |
| **Retry policy** | Exponential backoff, network errors only (2 retries). PLUS one self-correcting re-prompt on malformed JSON. |
| **Circuit breaker** | `llm_breaker` — `policy.resilience.llm_breaker.{fail_max, reset_timeout}` |
| **Failure behavior** | **Degrade → Escalate.** On second parse failure / timeout / breaker-open / `LLM_ENABLED=false`, fall back to the rule engine with `confidence=0.50` (below `policy.decision.confidence_gate`), so a pure fallback deterministically **escalates** downstream. |
