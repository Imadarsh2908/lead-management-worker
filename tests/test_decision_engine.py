"""
tests/test_decision_engine.py
-------------------------------
Unit tests for the Decision Engine business rules.
Tests are isolated — no database or external APIs needed.
"""
import pytest

from app.agents.decision_engine import DecisionEngine, LeadContext


class TestDecisionEngine:

    def setup_method(self):
        """Create a fresh engine before each test."""
        self.engine = DecisionEngine()

    # ── Guardrail Tests ────────────────────────────────────

    def test_missing_email_halts_all_processing(self):
        """Missing email must halt immediately — no routing or priority rules should run."""
        context = LeadContext(budget=1_000_000, job_title="CEO", ai_confidence=0.99, email=None)
        decision = self.engine.process_lead(context)

        assert decision.action == "ASK_USER"
        assert decision.halt_execution is True
        # Priority should be UNASSIGNED — no priority rules ran
        assert decision.priority == "UNASSIGNED"
        assert any("email" in r.lower() for r in decision.reasoning)

    def test_low_confidence_escalates_regardless_of_priority(self):
        """Even a high-budget CEO should escalate if AI confidence is below threshold."""
        context = LeadContext(
            email="ceo@bigcorp.com",
            budget=999_999,
            job_title="CEO",
            ai_confidence=0.50,  # Below 70% threshold
        )
        decision = self.engine.process_lead(context)

        assert decision.action == "ESCALATE"
        assert decision.halt_execution is True
        assert "70%" in " ".join(decision.reasoning)

    # ── Routing Rule Tests ─────────────────────────────────

    def test_high_budget_sets_high_priority(self):
        """Budget exceeding 5L (500,000) should set priority to HIGH."""
        context = LeadContext(
            email="manager@corp.com",
            budget=600_000,
            job_title="Manager",
            ai_confidence=0.95,
        )
        decision = self.engine.process_lead(context)

        assert decision.priority == "HIGH"
        assert decision.action == "PROCEED"
        assert decision.halt_execution is False

    def test_decision_maker_routes_to_senior_sales(self):
        """CEO/VP/Director job titles should route to the Senior Sales queue."""
        for title in ["CEO", "VP of Engineering", "Director of Sales", "Founder"]:
            context = LeadContext(
                email="exec@corp.com",
                budget=100_000,  # Below 5L — tests routing independent of budget
                job_title=title,
                ai_confidence=0.90,
            )
            decision = self.engine.process_lead(context)
            assert decision.assigned_queue == "SENIOR_SALES", f"Failed for title: {title}"

    def test_freemail_sets_low_priority(self):
        """Gmail/Yahoo emails should default to LOW priority (likely B2C)."""
        context = LeadContext(
            email="user@gmail.com",
            budget=0.0,
            job_title="",
            ai_confidence=0.90,
            is_freemail=True,
        )
        decision = self.engine.process_lead(context)

        assert decision.priority == "LOW"

    def test_high_budget_and_decision_maker_combined(self):
        """High budget + decision maker = HIGH priority + Senior Sales queue."""
        context = LeadContext(
            email="ceo@enterprise.com",
            budget=2_000_000,
            job_title="CEO",
            ai_confidence=0.95,
        )
        decision = self.engine.process_lead(context)

        assert decision.priority == "HIGH"
        assert decision.assigned_queue == "SENIOR_SALES"
        assert decision.action == "PROCEED"
