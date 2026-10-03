from app.agents.human_approval import HumanApprovalWorkflowAgent
from app.agents.memory import MemoryPersonalizationAgent
from app.agents.rebalancing import PortfolioRebalancingAgent
from app.agents.research import ResearchAgent
from app.agents.risk_compliance import RiskComplianceAgent
from app.agents.sentiment import SentimentAnalysisAgent
from app.agents.trade_execution import TradeExecutionProposalAgent
from app.contracts.analysis import AgentStageResult, RecommendationPackage
from app.contracts.common import WorkflowState
from app.contracts.domain import PortfolioRecord
from app.contracts.workflow import OrchestrationResponse, PortfolioRebalanceRequest
from app.persistence.memory_store import WorkflowStore


class Orchestrator:
    def __init__(self, store: WorkflowStore) -> None:
        self.store = store
        self.memory_agent = MemoryPersonalizationAgent()
        self.research_agent = ResearchAgent()
        self.sentiment_agent = SentimentAnalysisAgent()
        self.rebalancing_agent = PortfolioRebalancingAgent()
        self.risk_agent = RiskComplianceAgent()
        self.trade_agent = TradeExecutionProposalAgent()
        self.approval_agent = HumanApprovalWorkflowAgent()

    async def run(self, request: PortfolioRebalanceRequest) -> OrchestrationResponse:
        self.store.add_audit_event(
            event_type="REQUEST_RECEIVED",
            correlation=request.correlation,
            actor_id=request.actor.actor_id,
            outcome="ACCEPTED",
        )
        self.store.save_portfolio(
            PortfolioRecord(
                client_profile=request.client_profile,
                account_profile=request.account_profile,
                portfolio_snapshot=request.portfolio_snapshot,
                allocation_target=request.allocation_target,
                risk_profile=request.risk_profile,
                updated_at=request.portfolio_snapshot.as_of,
                source="rebalance_request",
            )
        )

        stages: list[AgentStageResult] = []

        memory_stage, _memory = await self.memory_agent.run(request)
        research_stage, _research = await self.research_agent.run(request)
        sentiment_stage, _sentiment = await self.sentiment_agent.run(request)
        stages.extend([memory_stage, research_stage, sentiment_stage])

        rebalancing_stage, rebalancing = await self.rebalancing_agent.run(request)
        stages.append(rebalancing_stage)

        current_allocation = rebalancing["current_allocation"]
        drift = rebalancing["drift"]
        risk_stage, risk_policy = await self.risk_agent.run(
            request.portfolio_snapshot, drift, request.risk_profile
        )
        stages.append(risk_stage)

        trade_stage, proposal = await self.trade_agent.run(request.portfolio_snapshot, risk_policy)
        stages.append(trade_stage)

        workflow_state = WorkflowState.NORMAL
        approval_eligibility = proposal.proposal_status in {"READY_FOR_REVIEW", "NO_ACTION_NEEDED"}
        if proposal.proposal_status == "BLOCKED":
            workflow_state = WorkflowState.BLOCKED
            approval_eligibility = False
        elif proposal.proposal_status == "NO_ACTION_NEEDED":
            workflow_state = WorkflowState.LOW_CONFIDENCE

        recommendation = RecommendationPackage(
            summary=self._summary(proposal.proposal_status),
            agent_stages=stages,
            current_allocation=current_allocation,
            target_allocation=request.allocation_target.asset_class_targets,
            proposed_allocation=proposal.estimated_impact or current_allocation,
            risk_policy=risk_policy,
            proposal=proposal,
            workflow_state=workflow_state,
            approval_eligibility=approval_eligibility,
            evidence=risk_policy.evidence,
        )
        _approval_stage, approval = await self.approval_agent.run(
            request.correlation, recommendation, request
        )
        approval.recommendation = recommendation
        persisted_approval = self.store.save_approval(approval)
        self.store.add_audit_event(
            event_type="APPROVAL_ARTIFACT_CREATED",
            correlation=request.correlation,
            actor_id=request.actor.actor_id,
            outcome=persisted_approval.approval_status,
            details={"approval_id": persisted_approval.approval_id},
        )

        return OrchestrationResponse(
            correlation=request.correlation,
            version=request.version,
            workflow_state=workflow_state,
            recommendation_package=persisted_approval.recommendation or recommendation,
            approval_artifact=persisted_approval,
        )

    def _summary(self, proposal_status: str) -> str:
        if proposal_status == "BLOCKED":
            return "Recommendation is blocked by deterministic policy checks."
        if proposal_status == "NO_ACTION_NEEDED":
            return "Portfolio is already within configured allocation tolerances."
        return "Portfolio has drift outside tolerance and is ready for manual review."
