import { HttpClient } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';

import {
  ApprovalTransitionResult,
  OrchestrationResponse,
  PortfolioRebalanceRequest
} from './rebalance.models';
import { apiUrl } from './api-config';

@Injectable({ providedIn: 'root' })
export class RebalanceService {
  private readonly http = inject(HttpClient);

  submit(request: PortfolioRebalanceRequest) {
    const idempotencyKey =
      request.correlation?.idempotency_key || request.correlation?.request_id || crypto.randomUUID();
    const headers: Record<string, string> = {
      'Idempotency-Key': idempotencyKey,
    };
    if (request.correlation?.session_id) {
      headers['X-Session-ID'] = request.correlation.session_id;
    }
    return this.http.post<OrchestrationResponse>(apiUrl('/rebalance'), request, {
      headers,
    });
  }

  approve(approvalId: string, recommendationHash: string) {
    return this.http.post<ApprovalTransitionResult>(apiUrl(`/approvals/${approvalId}/actions`), {
      action: 'APPROVE',
      actor_id: 'local_owner',
      expected_recommendation_hash: recommendationHash
    });
  }

  reject(approvalId: string, recommendationHash: string, note: string) {
    return this.http.post<ApprovalTransitionResult>(apiUrl(`/approvals/${approvalId}/actions`), {
      action: 'REJECT',
      actor_id: 'local_owner',
      note,
      expected_recommendation_hash: recommendationHash
    });
  }
}
