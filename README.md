# Autonomous Lead Management AI Worker

An open-source-first, AI-native autonomous backend that manages sales leads from ingestion to resolution — without human intervention for the common case.

The LangGraph agent validates, enriches, scores, and routes every inbound lead through a deterministic rule engine followed by an LLM classifier, while persisting a full audit trail.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Agent | LangGraph `StateGraph` |
| LLM | OpenAI GPT-3.5 (configurable) |
| Workflow State | Redis (LangGraph `RedisSaver` checkpointer) |
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
# Edit .env — set OPENAI_API_KEY and SECRET_KEY at minimum
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

> _This section will be filled in during a later phase with a step-by-step walkthrough video / screenshot guide._

The fastest path to a working end-to-end demo:

1. Start the backend (`docker-compose up -d` or `uvicorn app.main:app --port 8000`).
2. Start the frontend (`cd frontend && npm run dev`).
3. Open http://localhost:5173, log in as `admin_user` / `password123`.
4. Click **Ingest Lead**, submit any email, and watch the **Agent Processing Monitor** for live step-by-step execution.

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
