"""
app/agents/decision_engine.py
------------------------------
Rule-based decision engine that determines the priority and next action for a lead.

Architecture: Strategy / Chain of Responsibility Pattern
  Each business rule is an isolated class.
  - New rules are added by creating a new class — existing code is NEVER modified.
  - Rules are evaluated in order; critical guardrails (missing data, low confidence)
    run FIRST to short-circuit before reaching routing/priority rules.

This design is specifically maintainable because non-technical product managers
can read the rule classes and understand the logic without touching core code.
"""
from abc import ABC, abstractmethod
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────
# INPUT / OUTPUT SCHEMAS
# ─────────────────────────────────────────────────────────────

class LeadContext(BaseModel):
    """
    Structured input to the Decision Engine.
    Populated from the AgentState.memory dict after enrichment completes.
    """
    email: Optional[str] = None
    budget: float = 0.0
    job_title: str = ""
    ai_confidence: float = 1.0        # Set by the LLM scoring node
    company_size: Optional[str] = None  # e.g., "Enterprise", "SMB"
    is_freemail: bool = False          # Gmail/Yahoo/Hotmail = likely B2C


class DecisionOutput(BaseModel):
    """
    Structured decision returned by the engine.
    The LangGraph routing edge reads these fields to decide the next node.
    """
    priority: str = "UNASSIGNED"
    action: str = "PROCEED"            # "PROCEED", "ASK_USER", "ESCALATE"
    assigned_queue: str = "GENERAL_SALES"
    reasoning: List[str] = Field(default_factory=list)
    halt_execution: bool = False        # If True, stop processing further rules


# ─────────────────────────────────────────────────────────────
# RULE BASE CLASS
# ─────────────────────────────────────────────────────────────

class BaseRule(ABC):
    """Abstract base class for all business rules."""

    @abstractmethod
    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        """
        Reads the lead context and modifies the decision object in place.
        Returns the modified decision.
        """
        pass


# ─────────────────────────────────────────────────────────────
# GUARDRAIL RULES (run first — can halt execution early)
# ─────────────────────────────────────────────────────────────

class MissingEmailRule(BaseRule):
    """
    GUARDRAIL: If email is missing, there is nothing to do.
    Halt execution and prompt the user to provide it.
    """
    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        if not context.email:
            decision.action = "ASK_USER"
            decision.reasoning.append("Missing email address — paused to request from user.")
            decision.halt_execution = True  # No further rules are evaluated
        return decision


class LowConfidenceRule(BaseRule):
    """
    GUARDRAIL: If the AI scored this lead but isn't confident (< 70%),
    we defer to human judgment rather than taking autonomous action.
    """
    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        if context.ai_confidence < 0.70:
            decision.action = "ESCALATE"
            decision.reasoning.append(
                f"AI confidence ({context.ai_confidence:.0%}) is below the 70% threshold. "
                "Escalating to human for manual review."
            )
            decision.halt_execution = True
        return decision


# ─────────────────────────────────────────────────────────────
# ROUTING RULES (run after guardrails pass)
# ─────────────────────────────────────────────────────────────

class HighBudgetRule(BaseRule):
    """
    BUSINESS RULE: Budget > 5 Lakh (500,000) → High Priority.
    These leads represent the most significant revenue opportunity.
    """
    HIGH_BUDGET_THRESHOLD = 500_000  # INR 5L or USD 500K

    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        if context.budget > self.HIGH_BUDGET_THRESHOLD:
            decision.priority = "HIGH"
            decision.reasoning.append(
                f"Budget ₹{context.budget:,.0f} exceeds the ₹5L threshold → High Priority."
            )
        return decision


class DecisionMakerRoutingRule(BaseRule):
    """
    BUSINESS RULE: If the contact is a Decision Maker (CEO, VP, Director),
    route to the Senior Sales queue for white-glove handling.
    """
    DECISION_MAKER_TITLES = {"ceo", "founder", "vp", "director", "cmo", "cto", "coo", "president"}

    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        title_lower = (context.job_title or "").lower()
        # Check if ANY decision-maker keyword appears in the job title
        if any(kw in title_lower for kw in self.DECISION_MAKER_TITLES):
            decision.assigned_queue = "SENIOR_SALES"
            decision.reasoning.append(
                f"Job title '{context.job_title}' identified as a Decision Maker → Senior Sales queue."
            )
            # Decision makers are at least Medium priority even with low budget
            if decision.priority == "UNASSIGNED":
                decision.priority = "MEDIUM"
        return decision


class FreemailDowngradeRule(BaseRule):
    """
    BUSINESS RULE: Freemail addresses (Gmail, Yahoo) often indicate B2C leads
    which are not the target demographic. Downgrade to LOW priority.
    """
    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        if context.is_freemail and decision.priority == "UNASSIGNED":
            decision.priority = "LOW"
            decision.reasoning.append(
                f"Email domain is a freemail provider → likely B2C → Low Priority."
            )
        return decision


# ─────────────────────────────────────────────────────────────
# DECISION ENGINE ORCHESTRATOR
# ─────────────────────────────────────────────────────────────

class DecisionEngine:
    """
    Orchestrates the evaluation of business rules in the correct priority order.
    
    Usage:
        engine = DecisionEngine()
        context = LeadContext(email="ceo@bigcorp.com", budget=750000, job_title="CEO")
        result = engine.process_lead(context)
    """

    def __init__(self):
        # ORDER MATTERS: Guardrails run first, routing rules run last.
        # If a guardrail sets halt_execution=True, no further rules are processed.
        self.rules: List[BaseRule] = [
            MissingEmailRule(),         # Guardrail 1: Can we even process this lead?
            LowConfidenceRule(),         # Guardrail 2: Is the AI confident enough to act?
            HighBudgetRule(),            # Routing 1: High-value opportunity?
            DecisionMakerRoutingRule(),  # Routing 2: Is this the right buyer persona?
            FreemailDowngradeRule(),     # Routing 3: Is this likely a B2C lead?
        ]

    def process_lead(self, context: LeadContext) -> DecisionOutput:
        """
        Runs the lead context through all registered rules sequentially.
        Stops early if a guardrail sets halt_execution=True.
        """
        logger.info(f"Decision Engine evaluating lead: {context.email}")
        decision = DecisionOutput()

        for rule in self.rules:
            decision = rule.evaluate(context, decision)

            if decision.halt_execution:
                logger.info(
                    f"Execution halted by {rule.__class__.__name__}. "
                    f"Action: {decision.action}"
                )
                break

        # Final fallback: if no rules set a priority, mark as Medium
        if decision.priority == "UNASSIGNED" and not decision.halt_execution:
            decision.priority = "MEDIUM"
            decision.reasoning.append("No specific priority rules matched → defaulting to Medium.")

        logger.info(
            f"Decision Engine result: priority={decision.priority}, "
            f"action={decision.action}, queue={decision.assigned_queue}"
        )
        return decision
