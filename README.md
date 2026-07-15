# Autonomous Lead Management Worker

An open-source-first autonomous backend agent that takes a raw inbound sales lead and drives it to a decision — validate, dedupe, enrich, score with an LLM, then a deterministic rule guardrail that can only make the outcome *stricter* — drafting a follow-up and notifying sales for strong leads, and escalating to a human whenever it is missing data or unsure. Built on LangGraph orchestration behind a FastAPI surface, with Redis/Postgres persistence and a full, queryable audit trail. Every business constant and every line of the agent's instructions live in editable files, not in code.

## Quickstart

Three commands to a working, fully-offline run (no Postgres/Redis/LLM key needed):

```bash
pip install -r requirements.txt                 # 1. install
bash demo/serve_demo.sh                          # 2. start an offline SQLite server (LLM disabled)
python demo/run_demo.py llm_dead                 # 3. run a scenario (exits 0 on the expected outcome)
```

## Demo scenarios

Each scenario is proven by a single command; the harness logs in, submits a lead, polls to a terminal state, prints the audit timeline, and **exits non-zero if the outcome doesn't match** — so it doubles as a smoke test. Full details and per-scenario server env are in [Running the Demo](#running-the-demo); the shot-by-shot video script is [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md).

| Scenario | Proves | Command |
|---|---|---|
| `happy_path` | Autonomous end-to-end scoring → COMPLETED, HIGH | `python demo/run_demo.py happy_path` |
| `missing_email` | Escalate on missing contact info | `python demo/run_demo.py missing_email` |
| `enrichment_down` | Degrade, don't fabricate | `python demo/run_demo.py enrichment_down` |
| `llm_malformed` | Self-correcting re-prompt | `python demo/run_demo.py llm_malformed` |
| `llm_dead` | Rules fallback + confidence gate → escalate *(offline)* | `python demo/run_demo.py llm_dead` |
| `low_confidence` | Confidence gate / human-in-the-loop | `python demo/run_demo.py low_confidence` |
| `duplicate_lead` | Idempotent ingestion (409) *(offline)* | `python demo/run_demo.py duplicate_lead` |

## How the agent is instructed

All agent behavior is externalized — edit these files, not the code:

- **[SOUL.md](SOUL.md)** — the worker's identity and six inviolable guardrails (the behavioral source of truth).
- **[AGENTS.md](AGENTS.md)** — the LLM scoring prompt: role, JSON output schema, rubric, and few-shot examples (hot-reloaded on edit).
- **[TOOLS.md](TOOLS.md)** — contract table for each tool: purpose, I/O schema, timeout, retry, circuit breaker, failure behavior.
- **[config/policy.yaml](config/policy.yaml)** — every tunable business constant (budget threshold, confidence gate, retry ceiling, breaker settings), validated at startup.

## Learn more

- **Architecture** — graph flow, state model, persistence, API surface → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Migrating to an agent harness** (OpenClaw / NemoClaw / Hermes) → [docs/MIGRATION.md](docs/MIGRATION.md)
- **Roadmap** — what v1 does autonomously, what v2 improves → [docs/ROADMAP.md](docs/ROADMAP.md)
- **Submission checklist** — every deliverable mapped to a file/command → [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md)

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Agent | LangGraph `StateGraph` |
| LLM | OpenRouter, OpenAI-compatible (open-weight default: `qwen/qwen-2.5-7b-instruct`, configurable) |
| Workflow State | Redis (LangGraph `RedisSaver` checkpointer, `MemorySaver` fallback) |
| Storage | PostgreSQL / SQLite (SQLAlchemy 2) |
| Auth | JWT HS256 + RBAC (Admin / Sales / Operator) |
| Logging | Loguru JSON structured |
| Resilience | Tenacity + PyBreaker |
| Frontend | React + Vite |
| Tests | Pytest + HTTPX |

---

## Repo Layout

```
.
├── app/                          # Backend (FastAPI + LangGraph)
│   ├── agents/                   # Graph nodes, state model, decision engine, tools
│   │   └── tools/
│   ├── api/v1/                   # Route handlers (auth, leads)
│   ├── core/                     # Config, DB, Redis, security, logging, resilience
│   ├── models/                   # SQLAlchemy ORM models
│   ├── schemas/                  # Pydantic request/response schemas
│   └── utils/                    # Shared helpers (audit writer)
├── docs/                         # Engineering documentation
│   ├── ARCHITECTURE.md           # System overview, graph diagram, state model, DB schema
│   ├── DECISIONS.md              # Key design decisions and trade-offs
│   └── FAILURE_HANDLING.md      # Retry, circuit-breaker, degradation, escalation policy
├── frontend/                     # React + Vite SPA
│   └── src/
│       ├── components/
│       ├── context/
│       └── services/
├── tests/                        # Pytest suite (unit + integration)
├── alembic/                      # Database migrations
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example                  # Copy to .env to configure locally
└── README.md
```

---

## Quick Start

### 1. Clone and configure

```bash
git clone <your-repo-url>
cd lead-management-worker
cp .env.example .env
# Edit .env — set LLM_API_KEY (OpenRouter) and SECRET_KEY at minimum
# (OPENAI_API_KEY is still read as a deprecated fallback)
```

### 2. Run with Docker Compose

```bash
docker-compose up -d --build
```

### 3. Access the API

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc
- **Health check:** http://localhost:8000/health

---

## Running the Demo

A scripted CLI harness (`demo/run_demo.py`) drives the agent through seven
scenarios end-to-end over HTTP: it logs in, submits a lead, polls the workflow to
a terminal state, and pretty-prints the audit timeline. **Exit code is non-zero
if the observed outcome doesn't match the scenario's expectation, so it doubles
as a smoke test.** Golden input/output examples for each scenario live in
`demo/examples/<scenario>/`; a full video walkthrough is in
[demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md).

### 1. Start a server

Zero-dependency offline server (SQLite, no Postgres/Redis; enables demo seams):

```bash
bash demo/serve_demo.sh                      # ENVIRONMENT=development, SQLite, LLM disabled
```

Some scenarios need extra env (shown in the table). Layer it on the launch, e.g.:

```bash
LLM_ENABLED=true LLM_API_KEY=sk-or-... bash demo/serve_demo.sh
```

Or use the full stack: `docker-compose up -d` (Postgres + Redis) with a real `.env`.

### 2. Run a scenario

```bash
python demo/run_demo.py <scenario> [--base-url http://127.0.0.1:8000] [--timeout 30]
```

### Scenario table

| Scenario | Requirement | Expected outcome | Server env needed |
|---|---|---|---|
| `happy_path` | R1 — Autonomous end-to-end scoring | **COMPLETED**, HIGH, follow-up drafted | `LLM_ENABLED=true`, `LLM_API_KEY=…` |
| `missing_email` | R2 — Escalate on missing contact info | **ESCALATED** (via demo seam `/v1/leads/raw`) | `ENVIRONMENT=development` |
| `enrichment_down` | R3 — Degrade, don't fabricate | **COMPLETED**, `enrichment_failed=True` | `ENRICHMENT_API_URL=http://127.0.0.1:9`, LLM on |
| `llm_malformed` | R4 — Self-correcting re-prompt | **COMPLETED**, `source=llm_selfcorrected` | `LLM_FORCE_MALFORMED=true`, LLM on |
| `llm_dead` | R5 — Rules fallback + confidence gate | **ESCALATED**, `source=rules_fallback` | `LLM_ENABLED=false` *(offline)* |
| `low_confidence` | R6 — Confidence gate / HITL | **ESCALATED** | `LLM_FORCE_CONFIDENCE=0.40`, `ENVIRONMENT=development`, LLM on |
| `duplicate_lead` | R7 — Idempotent ingestion | first **202**, resubmit **409** | *(none; offline)* |

**Fully offline** (no LLM key): `llm_dead`, `duplicate_lead`, `missing_email`.
Regenerate the golden examples any time with `python demo/capture_examples.py`.

> **Demo seams (env-guarded, inert by default):** `POST /v1/leads/raw` +
> `X-Demo-Raw: true` (only when `ENVIRONMENT=development`) accepts an emailless
> payload to exercise the validation-escalation path; `LLM_FORCE_CONFIDENCE`
> pins model confidence (dev-only). See [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md).

### Frontend (optional, visual)

1. Start the backend, then `cd frontend && npm run dev`.
2. Open http://localhost:5173, log in as `admin_user` / `password123`.
3. Click **Ingest Lead**, submit a lead, and watch the **Agent Processing Monitor**.

---

## Running Tests

```bash
pip install -r requirements.txt
pytest
```

---

## Documentation

| Document | Description |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Graph flow, state model, persistence layers, API surface |
| [DECISIONS.md](docs/DECISIONS.md) | Design decisions and trade-offs |
| [FAILURE_HANDLING.md](docs/FAILURE_HANDLING.md) | Retry strategy, circuit breakers, escalation policy |
| [MIGRATION.md](docs/MIGRATION.md) | Mapping this workflow onto an agent harness (OpenClaw / NemoClaw / Hermes) |
| [ROADMAP.md](docs/ROADMAP.md) | What v1 does autonomously; what v2 improves |
| [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) | Every deliverable mapped to a file path / demo command |
