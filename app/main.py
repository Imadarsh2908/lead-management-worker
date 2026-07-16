"""
app/main.py
------------
FastAPI application entry point.

Responsibilities:
  1. Configure logging (Loguru JSON structured logging)
  2. Register startup/shutdown lifecycle hooks
  3. Mount all API routers
  4. Configure CORS, global exception handlers, and middleware
  5. Expose the /health endpoint for Docker health checks

Run locally (without Docker):
    uvicorn app.main:app --reload --port 8000

Run in production (via Docker CMD):
    gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.logging_config import setup_logging
from app.core.database import create_all_tables, SessionLocal
from app.core.seed import seed_demo_users
from app.api.v1 import auth, leads, users


# ─────────────────────────────────────────────────────────────
# LIFESPAN HANDLER
# Runs startup logic before the server starts accepting requests,
# and teardown logic after the server shuts down.
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle manager.
    
    Startup sequence:
      1. Configure structured JSON logging
      2. Create DB tables (idempotent — safe to run on every startup)
      3. Start the background scheduler (scheduled-email dispatcher)
      4. Application begins accepting requests

    Shutdown sequence:
      5. Stop the scheduler, then any other cleanup
    """
    # ── STARTUP ──────────────────────────────────────
    setup_logging()
    logger.info("Starting Lead Management Worker API...")

    # Creates tables if they don't exist (in production: use Alembic instead)
    from app.core.config import settings
    if settings.ENVIRONMENT != "testing":
        create_all_tables()
        logger.info("Database tables verified/created.")

        db = SessionLocal()
        try:
            seed_demo_users(db)
        finally:
            db.close()
    else:
        logger.info("Running in testing environment - skipping database table creation.")

    # Start the scheduled-email dispatcher (no-op in the testing environment).
    from app.core.scheduler import start_scheduler
    start_scheduler()

    logger.info("Application startup complete. Ready to accept requests.")
    yield  # <── The application runs while blocked here

    # ── SHUTDOWN ──────────────────────────────────────
    logger.info("Application shutting down. Cleaning up resources...")
    from app.core.scheduler import stop_scheduler
    stop_scheduler()


# ─────────────────────────────────────────────────────────────
# APPLICATION FACTORY
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Autonomous Lead Management Worker",
    description=(
        "An open-source-first, AI-native autonomous agent that manages leads "
        "from ingestion to resolution without human intervention.\n\n"
        "**Authentication:** Use POST /v1/auth/login to get a Bearer token, "
        "then click the 🔒 Authorize button above to use protected endpoints.\n\n"
        "**Demo credentials:** username=`admin_user`, password=`password123`"
    ),
    version="1.0.0",
    docs_url="/docs",    # Swagger UI
    redoc_url="/redoc",  # ReDoc alternative
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────
# MIDDLEWARE
# ─────────────────────────────────────────────────────────────

# CORS: allows frontend apps (e.g., React dashboard) to call this API
# In production: replace "*" with your specific frontend domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # Change to ["https://yourdomain.com"] in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
# GLOBAL EXCEPTION HANDLER
# Catches unhandled exceptions so they don't return ugly 500 HTML responses.
# ─────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catches any unhandled exception and returns a clean JSON error response.
    Logs the full traceback for debugging via structured logs.
    """
    logger.error(f"Unhandled exception on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": "An unexpected error occurred. It has been logged.",
        },
    )


# ─────────────────────────────────────────────────────────────
# ROUTERS
# Each router handles a specific domain (auth, leads, etc.)
# ─────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(leads.router)
app.include_router(users.router)


# ─────────────────────────────────────────────────────────────
# UTILITY ENDPOINTS
# ─────────────────────────────────────────────────────────────

@app.get(
    "/health",
    tags=["Health"],
    summary="Health check for Docker and load balancers",
    response_description="Returns 200 OK when the application is running",
)
def health_check():
    """
    Public health check endpoint (no authentication required).
    Used by Docker Compose health checks and Kubernetes liveness probes.
    """
    return {"status": "ok", "service": "lead-management-worker"}


@app.get("/", tags=["Root"], include_in_schema=False)
def root():
    """Redirects visitors to the Swagger docs."""
    return {"message": "Lead Management Worker API — visit /docs for Swagger UI"}
