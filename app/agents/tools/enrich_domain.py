"""
app/agents/tools/enrich_domain.py
-----------------------------------
Tool: EnrichDomainTool

Extracts a domain from an email address and calls an enrichment API
to fetch company metadata (name, industry, company size).

Resilience features:
  - Freemail detection: Gmail/Yahoo emails bypass the API call entirely
  - Tenacity retry: 3 attempts with exponential backoff for network failures
  - Graceful degradation: if API is down after retries, returns empty fields
    so the workflow continues rather than crashing
"""
from typing import Any, Optional, Type

import requests
from loguru import logger
from pydantic import BaseModel, Field, EmailStr
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)

from app.agents.tools.base import BaseAgentTool
from app.core.config import settings


# ─────────────────────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────────────────────

class EnrichDomainInput(BaseModel):
    """Input schema — LangGraph agent must provide a valid email."""
    email: EmailStr = Field(..., description="Email address of the lead to enrich.")


class EnrichDomainOutput(BaseModel):
    """Output schema — always returned even when enrichment fails."""
    domain: str = Field(..., description="Extracted domain from the email.")
    company_name: Optional[str] = Field(None, description="Company name from enrichment API.")
    industry: Optional[str] = Field(None, description="Industry category.")
    company_size: Optional[str] = Field(None, description="e.g., 'Enterprise', 'SMB', 'Startup'.")
    is_freemail: bool = Field(..., description="True if domain is Gmail/Yahoo/Hotmail etc.")
    enrichment_failed: bool = Field(
        default=False,
        description="True if the API call failed — signals the LLM to account for missing data.",
    )


# ─────────────────────────────────────────────────────────────
# FREE MAIL DETECTION
# ─────────────────────────────────────────────────────────────

FREEMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "protonmail.com", "aol.com",
}


# ─────────────────────────────────────────────────────────────
# TOOL IMPLEMENTATION
# ─────────────────────────────────────────────────────────────

class EnrichDomainTool(BaseAgentTool):
    """
    Agent tool that enriches a lead's email by fetching company context.
    
    When to use: Call this in the ENRICHMENT node when the lead has an email
    but we don't know the company's size or industry.
    """
    name: str = "enrich_lead_domain"
    description: str = (
        "Extracts the domain from an email and fetches company enrichment data "
        "(industry, size, company name). Use this when you have an email but "
        "lack company context needed for lead scoring."
    )
    args_schema: Type[BaseModel] = EnrichDomainInput
    response_schema: Type[BaseModel] = EnrichDomainOutput

    @retry(
        stop=stop_after_attempt(3),
        # Waits 1s, then 2s, then 4s between retries
        wait=wait_exponential(multiplier=1, min=1, max=8),
        # Only retry on network-level errors — don't retry on 404, 401 etc.
        retry=retry_if_exception_type(
            (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
        ),
        reraise=True,
    )
    def _call_enrichment_api(self, domain: str) -> dict:
        """
        Inner function: makes the actual HTTP request.
        Separated so Tenacity retries ONLY this function, not the full tool.
        """
        url = f"{settings.ENRICHMENT_API_URL}?domain={domain}"
        # Strict 5-second timeout — never block the agent indefinitely
        response = requests.get(url, timeout=5.0)
        response.raise_for_status()  # Raises HTTPError for 4xx/5xx responses
        return response.json()

    def _execute_tool(self, **kwargs: Any) -> EnrichDomainOutput:
        """Execute the enrichment tool.

        The BaseAgentTool expects a generic ``**kwargs`` signature, so we accept any
        mapping and extract the ``email`` parameter. This satisfies the mypy
        override requirement.
        """
        email = kwargs.get("email")
        if not isinstance(email, str):
            raise ValueError("'email' argument must be provided as a string")

        domain = email.split("@")[-1].lower()
        is_freemail = domain in FREEMAIL_PROVIDERS

        # Short‑circuit for freemail providers.
        if is_freemail:
            logger.info(f"Domain '{domain}' is a freemail provider. Skipping enrichment API.")
            return EnrichDomainOutput(
                domain=domain,
                is_freemail=True,
                enrichment_failed=False,
            )

        # Attempt enrichment API call with retry.
        try:
            api_data = self._call_enrichment_api(domain)
            logger.info(f"Enrichment API succeeded for domain: {domain}")
            return EnrichDomainOutput(
                domain=domain,
                company_name=api_data.get("name"),
                industry=api_data.get("industry"),
                company_size=api_data.get("size"),
                is_freemail=False,
                enrichment_failed=False,
            )
        except (RetryError, requests.exceptions.HTTPError, Exception) as e:
            # Graceful degradation – return partial data.
            logger.warning(f"Enrichment failed for domain '{domain}' after retries: {e}")
            return EnrichDomainOutput(
                domain=domain,
                is_freemail=False,
                enrichment_failed=True,
            )
