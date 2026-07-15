"""
app/agents/decision_engine.py
------------------------------
Rule-based decision engine that determines the priority and next action for a lead.

Behavioral source of truth: SOUL.md (principles) + config/policy.yaml (the exact
numbers these rules enforce). All thresholds/lists are read from get_policy() —
do NOT hardcode business constants here.

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

from app.core.policy import get_policy


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
    # Set explicitly by the engine to one of the routing verbs the graph understands:
    #   "generate_follow_up" | "notify" | "ASK_USER" | "ESCALATE"
    # (the "notify" default is a fail-safe — process_lead always overwrites it).
    action: str = "notify"
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
    GUARDRAIL: If the AI scored this lead but isn't confident (below the
    configured confidence gate, policy.decision.confidence_gate), we defer to
    human judgment rather than taking autonomous action.
    """
    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        gate = get_policy().decision.confidence_gate
        if context.ai_confidence < gate:
            decision.action = "ESCALATE"
            decision.reasoning.append(
                f"AI confidence ({context.ai_confidence:.0%}) is below the {gate:.0%} threshold. "
                "Escalating to human for manual review."
            )
            decision.halt_execution = True
        return decision


# ─────────────────────────────────────────────────────────────
# ROUTING RULES (run after guardrails pass)
# ─────────────────────────────────────────────────────────────

class HighBudgetRule(BaseRule):
    """
    BUSINESS RULE: Budget at/above the configured threshold → High Priority.
    These leads represent the most significant revenue opportunity.
    Threshold: policy.decision.high_budget_threshold.
    """

    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        threshold = get_policy().decision.high_budget_threshold
        if context.budget > threshold:
            decision.priority = "HIGH"
            decision.reasoning.append(
                f"Budget ₹{context.budget:,.0f} exceeds the ₹{threshold:,.0f} threshold → High Priority."
            )
        return decision


class DecisionMakerRoutingRule(BaseRule):
    """
    BUSINESS RULE: If the contact is a Decision Maker (CEO, VP, Director, …),
    route to the Senior Sales queue for white-glove handling.
    Title keywords: policy.decision.decision_maker_titles.
    """

    def evaluate(self, context: LeadContext, decision: DecisionOutput) -> DecisionOutput:
        title_lower = (context.job_title or "").lower()
        titles = get_policy().decision_maker_title_set
        # Check if ANY decision-maker keyword appears in the job title
        if any(kw in title_lower for kw in titles):
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

        # ── Emit an explicit next action per outcome ──────────────────────
        # The guardrails (MissingEmailRule / LowConfidenceRule) already set
        # ASK_USER / ESCALATE and halted, so we only DERIVE an action when we
        # did NOT halt. This makes the "notify" (LOW/MEDIUM) branch reachable —
        # previously every lead defaulted to PROCEED → generate_follow_up.
        if not decision.halt_execution:
            if decision.priority == "HIGH" or decision.assigned_queue == "SENIOR_SALES":
                # High-value budget or a decision-maker persona → personalized follow-up.
                decision.action = "generate_follow_up"
            else:
                # MEDIUM / LOW priority → just notify sales, no bespoke draft.
                decision.action = "notify"

        logger.info(
            f"Decision Engine result: priority={decision.priority}, "
            f"action={decision.action}, queue={decision.assigned_queue}"
        )
        return decision
