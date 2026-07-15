"""
app/core/policy.py
------------------
Loads and validates config/policy.yaml — the single source of truth for every
tunable business constant the agent enforces (see SOUL.md for the principles).

Design:
  - Pydantic models validate the YAML at load time. A malformed or incomplete
    policy file raises immediately with a clear message (fail-fast at startup)
    rather than surfacing as a mysterious KeyError deep in a graph node.
  - get_policy() caches the parsed result so hot paths (every routing edge,
    every rule evaluation) don't re-read the file. Tests can override the path
    (POLICY_PATH env var / reload_policy()) to exercise behavior changes driven
    purely by editing the file.
"""
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import yaml
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator


# ─────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────

class BreakerConfig(BaseModel):
    fail_max: int = Field(gt=0)
    reset_timeout: int = Field(gt=0)


class DecisionPolicy(BaseModel):
    high_budget_threshold: float = Field(gt=0)
    confidence_gate: float = Field(ge=0.0, le=1.0)
    decision_maker_titles: List[str] = Field(min_length=1)
    freemail_domains: List[str] = Field(min_length=1)

    @field_validator("decision_maker_titles", "freemail_domains")
    @classmethod
    def _normalize_lower(cls, values: List[str]) -> List[str]:
        # Store normalized (lowercase, stripped) so comparisons are case-insensitive.
        return [v.strip().lower() for v in values]


class WorkflowPolicy(BaseModel):
    max_retries: int = Field(gt=0)


class ResiliencePolicy(BaseModel):
    crm_breaker: BreakerConfig
    enrichment_breaker: BreakerConfig
    llm_breaker: BreakerConfig
    enrichment_timeout_seconds: float = Field(gt=0)
    crm_timeout_seconds: float = Field(gt=0)


class Policy(BaseModel):
    decision: DecisionPolicy
    workflow: WorkflowPolicy
    resilience: ResiliencePolicy

    # Convenience: freemail domains as a set for O(1) membership checks.
    @property
    def freemail_domain_set(self) -> set:
        return set(self.decision.freemail_domains)

    @property
    def decision_maker_title_set(self) -> set:
        return set(self.decision.decision_maker_titles)


# ─────────────────────────────────────────────────────────────
# LOADING
# ─────────────────────────────────────────────────────────────

# Default location: <repo_root>/config/policy.yaml. Resolved relative to this
# file so it works regardless of the process CWD.
_DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "policy.yaml"


def _resolve_path() -> Path:
    """Policy path, overridable via the POLICY_PATH env var (used by tests)."""
    import os
    override = os.environ.get("POLICY_PATH")
    return Path(override) if override else _DEFAULT_POLICY_PATH


def _load_policy_from(path: Path) -> Policy:
    if not path.exists():
        raise RuntimeError(
            f"Policy file not found at '{path}'. Create config/policy.yaml or set POLICY_PATH."
        )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as e:
        raise RuntimeError(f"Policy file '{path}' is not valid YAML: {e}") from e

    if not isinstance(raw, dict):
        raise RuntimeError(f"Policy file '{path}' must contain a top-level mapping.")

    try:
        policy = Policy.model_validate(raw)
    except ValidationError as e:
        # Fail fast with the exact validation errors — no silent defaults.
        raise RuntimeError(f"Invalid policy configuration in '{path}':\n{e}") from e

    logger.info(f"[POLICY] Loaded and validated policy from {path}")
    return policy


@lru_cache(maxsize=1)
def get_policy() -> Policy:
    """Returns the cached, validated Policy. First call reads + validates the file."""
    return _load_policy_from(_resolve_path())


def reload_policy() -> Policy:
    """Clears the cache and re-reads the policy file. Primarily for tests."""
    get_policy.cache_clear()
    return get_policy()
