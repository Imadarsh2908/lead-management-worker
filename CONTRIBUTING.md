# Contributing to Lead Management Worker

Thank you for contributing! This guide helps you set up the project locally, run the tests, and mock the external services (such as the LLM, CRM, and Enrichment APIs) so you can develop without spending API credits or needing production credentials.

---

## 🛠️ Local Development Setup

### Prerequisites
- Python 3.11+
- SQLite (built-in)
- Docker & Docker Compose (optional, for Redis/PostgreSQL)

### Step 1: Clone and Create Virtual Environment
```bash
git clone <repo-url>
cd task
python -m venv venv
```

Activate the virtual environment:
- **Windows:** `venv\Scripts\activate`
- **macOS/Linux:** `source venv/bin/activate`

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Copy Environment Configuration
```bash
cp .env.example .env
```
The defaults in `.env.example` are set up for local development.

---

## 🧪 Testing and Mocking

We use `pytest` for the test suite. All tests are pre-configured to run against an in-memory SQLite database and mock all network calls to LLMs and third-party APIs.

### Running the Test Suite
You do not need Docker, Postgres, Redis, or OpenAI keys to run the tests. Simply execute:
```bash
python -m pytest
```

The configuration in `pytest.ini` enforces a **minimum 85% code coverage**.

---

## 🤖 Mocking the LLM locally for Development

If you want to run the FastAPI app locally (`uvicorn app.main:app --reload`) and test the LangGraph workflow without sending real requests to OpenAI:

### Option A: Use the Local decision_engine Mock
For local manual testing, the LangGraph `lead_score` node is already wired to run the rule-based `DecisionEngine` as a fallback when `OPENAI_API_KEY` is set to `"not-set"` (the default in `.env`). This simulates the classification outputs of an LLM using deterministic python rules.

### Option B: Spin up a local LLM API with LocalAI or vLLM
If you want to test actual LLM prompting locally:
1. Install [LocalAI](https://github.com/mudler/LocalAI) or [Ollama](https://ollama.com).
2. Start the local server (usually exposes `http://localhost:8080/v1` or similar compatible OpenAI endpoint).
3. Update your `.env`:
   ```env
   OPENAI_API_KEY=dummy-key
   OPENAI_API_BASE=http://localhost:8080/v1
   OPENAI_MODEL=your-local-model-name
   ```

---

## 🏢 Database Migrations (Alembic)

For local development, table creation is handled automatically on startup in `app/main.py` when `ENVIRONMENT` is not `"testing"`.

If you modify models in `app/models/lead.py` and need to generate migrations:
```bash
# Generate a new migration script
alembic revision --autogenerate -m "describe your changes"

# Apply migrations
alembic upgrade head
```
