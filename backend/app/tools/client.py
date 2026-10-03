import asyncio
import logging
from typing import Any, Optional

import httpx

from app.contracts.analysis import (
    ApprovalArtifact,
    AuditEvent,
    DriftItem,
    ExecutionProposalResponse,
    RiskPolicyResponse,
)
from app.contracts.common import CorrelationMetadata, new_id
from app.contracts.domain import (
    AllocationTarget,
    PortfolioRecord,
    PortfolioSnapshot,
    RiskProfile,
)
from app.core.config import Settings, get_settings
from app.persistence.memory_store import WorkflowStore
from app.services.policy import evaluate_policy as inprocess_evaluate_policy
from app.services.portfolio import (
    calculate_asset_allocation as inprocess_calculate_allocation,
    calculate_drift as inprocess_calculate_drift,
)
from app.services.proposal import generate_execution_proposal as inprocess_generate_proposal
from app.tools.models import (
    AuditRequest,
    ComputeDriftRequest,
    ComputeDriftResponse,
    EvaluatePolicyRequest,
    GenerateProposalRequest,
    GetPortfolioRequest,
    PersistProposalRequest,
)

logger = logging.getLogger(__name__)


def _get_id_token(audience: str) -> Optional[str]:
    """Fetch Google OIDC ID token for Cloud Run authenticated endpoint."""
    if not audience.startswith("http"):
        return None
    if "localhost" in audience or "127.0.0.1" in audience or "test" in audience:
        return None
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        auth_req = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(auth_req, audience)
    except Exception as e:
        logger.debug(f"Could not fetch Google ID token: {e}")
        return None


class ToolClient:
    """Client for deterministic portfolio calculations (inprocess or remote via Cloud Run)."""

    def __init__(
        self,
        mode: Optional[str] = None,
        base_url: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        store: Optional[WorkflowStore] = None,
        settings: Optional[Settings] = None,
        timeout: float = 10.0,
    ) -> None:
        self.settings = settings or get_settings()
        self.mode = mode or getattr(self.settings, "tool_mode", "inprocess")
        if http_client and not base_url:
            self.base_url = str(http_client.base_url).rstrip("/")
        else:
            self.base_url = (base_url or getattr(self.settings, "tools_url", "http://localhost:8000")).rstrip("/")
        self.http_client = http_client
        self.store = store
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Caller-Identity": "sa-runtime@mybrightday-dev.iam.gserviceaccount.com",
        }
        token = _get_id_token(self.base_url)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _post_with_retry(self, endpoint: str, json_data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/tools/{endpoint}"
        headers = self._get_headers()
        last_exception = None

        for attempt in range(2):
            try:
                if self.http_client:
                    resp = await self.http_client.post(
                        url, json=json_data, headers=headers, timeout=self.timeout
                    )
                else:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(url, json=json_data, headers=headers)

                if resp.status_code >= 500:
                    if attempt == 0:
                        logger.warning(
                            f"Tool endpoint {endpoint} returned 5xx ({resp.status_code}), retrying..."
                        )
                        await asyncio.sleep(0.5)
                        continue
                    resp.raise_for_status()

                resp.raise_for_status()
                return resp.json()

            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                # Do not retry on 4xx client errors
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    raise e
                last_exception = e
                if attempt == 0:
                    logger.warning(f"Tool endpoint {endpoint} failed: {e}, retrying...")
                    await asyncio.sleep(0.5)
                    continue
                raise e
            except Exception as e:
                last_exception = e
                if attempt == 0:
                    logger.warning(f"Tool endpoint {endpoint} failed: {e}, retrying...")
                    await asyncio.sleep(0.5)
                    continue
                raise e

        raise last_exception  # pragma: no cover

    async def get_portfolio(self, account_id: str) -> Optional[PortfolioRecord]:
        """Retrieve portfolio record for an account ID."""
        if self.mode == "remote":
            req = GetPortfolioRequest(account_id=account_id)
            try:
                data = await self._post_with_retry("get_portfolio", req.model_dump(mode="json"))
                return PortfolioRecord.model_validate(data)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return None
                raise
        else:
            if self.store:
                return self.store.get_portfolio(account_id)
            return None

    async def compute_drift(
        self, snapshot: PortfolioSnapshot, target: AllocationTarget
    ) -> ComputeDriftResponse:
        """Compute asset allocation and drift against target bands."""
        if self.mode == "remote":
            req = ComputeDriftRequest(portfolio_snapshot=snapshot, allocation_target=target)
            data = await self._post_with_retry("compute_drift", req.model_dump(mode="json"))
            return ComputeDriftResponse.model_validate(data)
        else:
            current = inprocess_calculate_allocation(snapshot)
            drift = inprocess_calculate_drift(snapshot, target)
            return ComputeDriftResponse(current_allocation=current, drift=drift)

    async def evaluate_policy(
        self,
        snapshot: PortfolioSnapshot,
        drift: list[DriftItem],
        risk_profile: Optional[RiskProfile],
    ) -> RiskPolicyResponse:
        """Evaluate risk policy rules against portfolio drift and concentration limits."""
        if self.mode == "remote":
            req = EvaluatePolicyRequest(
                portfolio_snapshot=snapshot,
                drift=drift,
                risk_profile=risk_profile,
            )
            data = await self._post_with_retry("evaluate_policy", req.model_dump(mode="json"))
            return RiskPolicyResponse.model_validate(data)
        else:
            return inprocess_evaluate_policy(
                snapshot=snapshot, drift=drift, risk_profile=risk_profile
            )

    async def generate_proposal(
        self, snapshot: PortfolioSnapshot, risk_policy: RiskPolicyResponse
    ) -> ExecutionProposalResponse:
        """Generate rebalancing trade proposals based on evaluated risk policy."""
        if self.mode == "remote":
            req = GenerateProposalRequest(
                portfolio_snapshot=snapshot,
                risk_policy=risk_policy,
            )
            data = await self._post_with_retry("generate_proposal", req.model_dump(mode="json"))
            return ExecutionProposalResponse.model_validate(data)
        else:
            return inprocess_generate_proposal(snapshot=snapshot, risk_policy=risk_policy)

    async def persist_proposal(self, artifact: ApprovalArtifact) -> ApprovalArtifact:
        """Persist an approval artifact to the operational store."""
        if self.mode == "remote":
            req = PersistProposalRequest(approval_artifact=artifact)
            data = await self._post_with_retry("persist_proposal", req.model_dump(mode="json"))
            return ApprovalArtifact.model_validate(data)
        else:
            if self.store:
                return self.store.save_approval(artifact)
            return artifact

    async def audit(
        self,
        event_type: str,
        correlation: CorrelationMetadata,
        outcome: str,
        actor_id: Optional[str] = None,
        details: Optional[dict[str, str]] = None,
    ) -> AuditEvent:
        """Record an immutable audit event."""
        if self.mode == "remote":
            req = AuditRequest(
                event_type=event_type,
                correlation=correlation,
                outcome=outcome,
                actor_id=actor_id,
                details=details,
            )
            data = await self._post_with_retry("audit", req.model_dump(mode="json"))
            return AuditEvent.model_validate(data)
        else:
            if self.store:
                return self.store.add_audit_event(
                    event_type=event_type,
                    correlation=correlation,
                    outcome=outcome,
                    actor_id=actor_id,
                    details=details,
                )
            from datetime import UTC, datetime

            return AuditEvent(
                event_id=new_id("evt"),
                event_type=event_type,
                correlation=correlation,
                actor_id=actor_id,
                outcome=outcome,
                details=details or {},
                created_at=datetime.now(UTC),
            )
