import { SlicePipe, TitleCasePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, OnDestroy, computed, inject, signal } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { Subscription, filter } from 'rxjs';

import { HealthResponse, HealthService } from './core/api/health.service';
import { MarketStreamEvent } from './core/api/market.models';
import { MarketStreamService } from './core/api/market-stream.service';
import { PortfolioService } from './core/api/portfolio.service';
import { PreferenceService } from './core/api/preference.service';
import {
  OrchestrationResponse,
  PortfolioRebalanceRequest,
  PortfolioRecord
} from './core/api/rebalance.models';
import { RebalanceService } from './core/api/rebalance.service';
import { apiUrl } from './core/api/api-config';

@Component({
  selector: 'app-root',
  imports: [ReactiveFormsModule, FormsModule, RouterOutlet, TitleCasePipe, SlicePipe],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnDestroy {
  private readonly formBuilder = inject(FormBuilder);
  private readonly healthService = inject(HealthService);
  private readonly marketStreamService = inject(MarketStreamService);
  private readonly portfolioService = inject(PortfolioService);
  private readonly preferenceService = inject(PreferenceService);
  private readonly rebalanceService = inject(RebalanceService);
  private readonly router = inject(Router);

  protected readonly title = signal('Asset Management');
  protected readonly backendHealth = signal<HealthResponse | null>(null);
  protected readonly backendHealthError = signal<string | null>(null);
  protected readonly recommendation = signal<OrchestrationResponse | null>(null);
  protected readonly approvalMessage = signal<string | null>(null);
  protected readonly balancedAccounts = signal<Record<string, boolean>>({});
  private readonly forcedDriftByAccount = signal<Record<string, boolean>>({});
  private readonly autoRecommendByAccount = signal<Record<string, boolean>>({});
  private readonly hasAutoSubmittedByAccount = signal<Record<string, boolean>>({});
  protected readonly rebalanceError = signal<string | null>(null);
  protected readonly currentSessionId = signal<string | null>(null);
  protected readonly marketEvent = signal<MarketStreamEvent | null>(null);
  protected readonly portfolios = signal<PortfolioRecord[]>([]);
  protected readonly selectedAccountId = signal<string>('acct_demo');
  protected readonly showMainContent = signal(true);
  protected readonly selectedPortfolio = computed(
    () =>
      this.portfolios().find(
        (portfolio) => portfolio.account_profile.account_id === this.selectedAccountId()
      ) ?? null
  );
  protected readonly selectedPortfolioBalanced = computed(() =>
    this.isAccountBalanced(this.selectedAccountId())
  );
  protected portfolioModeLabel(accountId: string): string {
    return this.isAccountBalanced(accountId) ? 'Balanced' : 'Drifting';
  }

  protected decisionSummary(status?: string): string {
    if (status === 'APPROVED') {
      return 'Approved and applied to the selected client portfolio.';
    }
    if (status === 'REJECTED') {
      return 'Rejected. Portfolio remains in drift simulation mode.';
    }
    return 'Awaiting approval decision.';
  }

  protected reviewHeadline(response: OrchestrationResponse): string {
    const trades = response.recommendation_package?.proposal?.trades ?? [];
    const status = response.approval_artifact?.approval_status ?? 'PENDING';
    const state = response.workflow_state;
    const verdict = response.recommendation_package?.risk_policy?.verdict;
    
    if (status === 'APPROVED') {
      return 'Recommendation approved and applied';
    }
    if (status === 'REJECTED') {
      return 'Recommendation rejected';
    }
    
    // Check for policy block
    if (state === 'BLOCKED' || verdict === 'NON_COMPLIANT') {
      return 'Recommendation blocked by policy checks';
    }
    
    if (trades.length > 0) {
      return `Rebalance required - ${trades.length} trade${trades.length === 1 ? '' : 's'} ready`;
    }
    return 'Portfolio within tolerance - no action required';
  }

  protected canReviewDecision(response: OrchestrationResponse): boolean {
    const status = response.approval_artifact?.approval_status;
    if (status !== 'PENDING') {
      return false;
    }
    const trades = response.recommendation_package?.proposal?.trades ?? [];
    const proposalStatus = response.recommendation_package?.proposal?.proposal_status;
    // Show approve/reject if there are trades OR if the proposal is ready for review
    return trades.length > 0 || proposalStatus === 'READY_FOR_REVIEW';
  }
  
  protected isBlockedByPolicy(response: OrchestrationResponse): boolean {
    const status = response.approval_artifact?.approval_status;
    if (status !== 'PENDING') {
      return false;
    }
    
    const state = response.workflow_state;
    const verdict = response.recommendation_package?.risk_policy?.verdict;
    
    return state === 'BLOCKED' || verdict === 'NON_COMPLIANT';
  }
  protected readonly showAgentStages = signal(false);

  protected toggleAgentStages() {
    this.showAgentStages.update((current) => !current);
  }
  protected stageTone(status: string): 'done' | 'running' | 'pending' {
    const normalized = status.toLowerCase();
    if (normalized.includes('complete') || normalized.includes('success')) {
      return 'done';
    }
    if (normalized.includes('process') || normalized.includes('progress') || normalized.includes('running')) {
      return 'running';
    }
    return 'pending';
  }
  protected readonly displayMarketEvent = computed(() => {
    const event = this.marketEvent();
    if (!event) {
      return null;
    }
    return this.selectedPortfolioBalanced() ? this.toBalancedMarketEvent(event) : event;
  });
  protected readonly marketStreamError = signal<string | null>(null);
  protected readonly submitting = signal(false);
  private marketSubscription?: Subscription;

  protected readonly form = this.formBuilder.nonNullable.group({
    displayLabel: ['Demo Investor', Validators.required],
    equityValue: [7000, [Validators.required, Validators.min(0)]],
    fixedIncomeValue: [2000, [Validators.required, Validators.min(0)]],
    cashValue: [1000, [Validators.required, Validators.min(0)]],
    targetEquityPct: [60, [Validators.required, Validators.min(0), Validators.max(100)]],
    targetFixedIncomePct: [30, [Validators.required, Validators.min(0), Validators.max(100)]],
    targetCashPct: [10, [Validators.required, Validators.min(0), Validators.max(100)]]
  });

  constructor() {
    // Listen to router events to toggle main content visibility and reload preferences
    this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe((event) => {
        const navEvent = event as NavigationEnd;
        const isMainPage = navEvent.url === '/' || navEvent.url === '';
        this.showMainContent.set(isMainPage);
        
        // Reload preferences when returning to main page
        if (isMainPage && this.selectedPortfolio()) {
          this.loadPreferencesForPortfolio(this.selectedPortfolio()!.client_profile.client_id);
        }
      });

    this.healthService.getHealth().subscribe({
      next: (health) => this.backendHealth.set(health),
      error: () => this.backendHealthError.set('Backend health check is not reachable yet.')
    });
    this.portfolioService.list().subscribe({
      next: (portfolios) => {
        this.portfolios.set(portfolios);
        const firstPortfolio = portfolios[0];
        if (firstPortfolio) {
          this.selectedAccountId.set(firstPortfolio.account_profile.account_id);
          this.patchFormFromPortfolio(firstPortfolio);
          this.loadPreferencesForPortfolio(firstPortfolio.client_profile.client_id);
          this.startMarketStream(firstPortfolio.account_profile.account_id);
        } else {
          this.startMarketStream();
        }
      },
      error: () => {
        this.marketStreamError.set('Stored portfolios are not reachable yet.');
        this.startMarketStream();
      }
    });
  }

  ngOnDestroy() {
    this.marketSubscription?.unsubscribe();
  }

  protected selectPortfolio(event: Event) {
    const accountId = (event.target as HTMLSelectElement).value;
    const portfolio = this.portfolios().find((item) => item.account_profile.account_id === accountId);
    if (!portfolio) {
      return;
    }
    this.selectedAccountId.set(accountId);
    this.currentSessionId.set(null);
    this.recommendation.set(null);
    this.hasAutoSubmittedByAccount.update(m => ({ ...m, [accountId]: false }));
    this.patchFormFromPortfolio(portfolio);
    this.loadPreferencesForPortfolio(portfolio.client_profile.client_id);
    this.startMarketStream(accountId);
  }

  protected submitRebalance() {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.submitting.set(true);
    this.rebalanceError.set(null);

    // Fetch latest preferences before submitting
    const clientId = this.selectedPortfolio()?.client_profile.client_id ?? 'client_demo';
    this.preferenceService.getPreferences(clientId).subscribe({
      next: (preferences) => {
        const request = this.buildRequestWithPreferences(preferences);
        this.submitRebalanceRequest(request);
      },
      error: () => {
        this.rebalanceError.set('Warning: Could not load preferences. Using default values.');
        const request = this.buildRequest();
        this.submitRebalanceRequest(request);
      }
    });
  }

  private submitRebalanceRequest(request: PortfolioRebalanceRequest) {
    this.rebalanceService.submit(request).subscribe({
      next: (response) => {
        if (response.correlation?.session_id) {
          this.currentSessionId.set(response.correlation.session_id);
        }
        this.recommendation.set(response);
        this.approvalMessage.set(null);
        this.chatMessages.set([]);
        this.setAccountBalanced(this.selectedAccountId(), false);
        this.clearForcedDriftForAccount(this.selectedAccountId());
        this.clearAutoRecommendForAccount(this.selectedAccountId());
        this.submitting.set(false);
      },
      error: () => {
        this.rebalanceError.set('Unable to submit rebalance request.');
        this.clearAutoRecommendForAccount(this.selectedAccountId());
        this.submitting.set(false);
      }
    });
  }

  protected driftEntries(event: MarketStreamEvent) {
    return Object.entries(event.monitoring.drift);
  }

  protected approveRecommendation() {
    const approval = this.recommendation()?.approval_artifact;
    if (!approval) {
      return;
    }
    this.rebalanceService.approve(approval.approval_id, approval.recommendation_hash).subscribe({
      next: (result) => {
        this.approvalMessage.set(`${result.message} Portfolio is now displayed as balanced.`);
        this.setAccountBalanced(this.selectedAccountId(), true);
        this.clearForcedDriftForAccount(this.selectedAccountId());
        this.updateApprovalStatus(result.next_status);
        const displayed = this.displayMarketEvent();
        if (displayed) {
          this.syncFormToMarket(displayed);
        }
      },
      error: () => this.approvalMessage.set('Approval action failed.')
    });
  }

  protected rejectRecommendation() {
    const approval = this.recommendation()?.approval_artifact;
    if (!approval) {
      return;
    }
    this.rebalanceService
      .reject(approval.approval_id, approval.recommendation_hash, 'Rejected from local UI.')
      .subscribe({
        next: (result) => {
          this.approvalMessage.set(`${result.message} Rebalance trigger remains active.`);
          this.setAccountBalanced(this.selectedAccountId(), false);
          this.updateApprovalStatus(result.next_status);
          const event = this.marketEvent();
          if (event) {
            this.syncFormToMarket(event);
          }
        },
        error: () => this.approvalMessage.set('Rejection action failed.')
      });
  }

  protected acknowledgeBlockedRecommendation() {
    const approval = this.recommendation()?.approval_artifact;
    if (!approval) {
      return;
    }
    // Acknowledge by rejecting with a specific message
    this.rebalanceService
      .reject(approval.approval_id, approval.recommendation_hash, 'Acknowledged policy block from local UI.')
      .subscribe({
        next: (result) => {
          this.approvalMessage.set(`${result.message} Policy block acknowledged. You may adjust preferences or portfolio to resolve the issue.`);
          this.setAccountBalanced(this.selectedAccountId(), false);
          this.updateApprovalStatus(result.next_status);
          this.recommendation.set(null); // Clear the blocked recommendation
          this.hasAutoSubmittedByAccount.update(m => ({ ...m, [this.selectedAccountId()]: false }));
          const event = this.marketEvent();
          if (event) {
            this.syncFormToMarket(event);
          }
        },
        error: () => this.approvalMessage.set('Acknowledgment action failed.')
      });
  }

  protected simulateDriftAgain() {
    const accountId = this.selectedAccountId();
    this.recommendation.set(null);
    this.showAgentStages.set(false);
    this.rebalanceError.set(null);
    this.approvalMessage.set(null);

    // Reset the auto-submit gate so the next drift cycle fires once
    this.hasAutoSubmittedByAccount.update(m => ({ ...m, [accountId]: false }));

    // Mark as unbalanced and enable forced drift
    this.setAccountBalanced(accountId, false);
    this.enableForcedDriftForAccount(accountId);

    // Apply forced drift immediately to the current market event
    const event = this.marketEvent();
    if (event) {
      const driftedEvent = this.toForcedDriftEvent(event);
      this.marketEvent.set(driftedEvent);
      this.syncFormToMarket(driftedEvent);
      // Trigger auto-generate immediately
      this.maybeAutoGenerateRecommendation(accountId);
    }
  }

  private buildRequest(): PortfolioRebalanceRequest {
    const value = this.form.getRawValue();
    const portfolio = this.selectedPortfolio();
    const asOf = new Date().toISOString();
    const totalValue = value.equityValue + value.fixedIncomeValue + value.cashValue;
    const clientProfile = portfolio?.client_profile;
    const accountProfile = portfolio?.account_profile;
    const allocationTarget = portfolio?.allocation_target;
    const riskProfile = portfolio?.risk_profile;

    return {
      correlation: {
        session_id: this.currentSessionId() ?? undefined,
      },
      actor: {
        actor_id: 'local_owner',
        display_name: 'Local Owner',
        role: 'OWNER',
        auth_provider: 'local',
        is_owner: true
      },
      client_profile: {
        client_id: clientProfile?.client_id ?? 'client_demo',
        display_label: value.displayLabel,
        risk_profile_id: clientProfile?.risk_profile_id ?? 'risk_balanced',
        synthetic: clientProfile?.synthetic ?? true
      },
      account_profile: {
        account_id: accountProfile?.account_id ?? 'acct_demo',
        client_id: accountProfile?.client_id ?? 'client_demo',
        account_type: accountProfile?.account_type ?? 'BROKERAGE',
        base_currency: accountProfile?.base_currency ?? 'USD',
        taxable: accountProfile?.taxable ?? true
      },
      portfolio_snapshot: {
        snapshot_id: `snap_${accountProfile?.account_id ?? 'acct_demo'}_${Date.now()}`,
        account_id: accountProfile?.account_id ?? 'acct_demo',
        as_of: asOf,
        holdings: [
          {
            instrument_id: 'inst_equity',
            symbol: 'EQUITY',
            asset_class: 'equity',
            quantity: '1',
            market_price: String(value.equityValue),
            market_value: String(value.equityValue),
            as_of: asOf
          },
          {
            instrument_id: 'inst_fixed_income',
            symbol: 'BONDS',
            asset_class: 'fixed_income',
            quantity: '1',
            market_price: String(value.fixedIncomeValue),
            market_value: String(value.fixedIncomeValue),
            as_of: asOf
          }
        ],
        cash: String(value.cashValue),
        total_value: String(totalValue)
      },
      allocation_target: {
        target_id: allocationTarget?.target_id ?? 'target_demo',
        account_id: accountProfile?.account_id ?? 'acct_demo',
        asset_class_targets: {
          equity: String(value.targetEquityPct),
          fixed_income: String(value.targetFixedIncomePct),
          cash: String(value.targetCashPct)
        },
        tolerance_bands: {
          equity: '5',
          fixed_income: '5',
          cash: '5'
        }
      },
      risk_profile: {
        risk_profile_id: riskProfile?.risk_profile_id ?? 'risk_balanced',
        risk_level: riskProfile?.risk_level ?? 'balanced',
        max_single_position_pct: riskProfile?.max_single_position_pct ?? '85',
        max_sector_pct: riskProfile?.max_sector_pct ?? '60',
        allowed_asset_classes: riskProfile?.allowed_asset_classes ?? ['equity', 'fixed_income', 'cash']
      }
    };
  }

  private buildRequestWithPreferences(preferences: any): PortfolioRebalanceRequest {
    const value = this.form.getRawValue();
    const portfolio = this.selectedPortfolio();
    const asOf = new Date().toISOString();
    const totalValue = value.equityValue + value.fixedIncomeValue + value.cashValue;
    const clientProfile = portfolio?.client_profile;
    const accountProfile = portfolio?.account_profile;

    return {
      correlation: {
        session_id: this.currentSessionId() ?? undefined,
      },
      actor: {
        actor_id: 'local_owner',
        display_name: 'Local Owner',
        role: 'OWNER',
        auth_provider: 'local',
        is_owner: true
      },
      client_profile: {
        client_id: clientProfile?.client_id ?? 'client_demo',
        display_label: value.displayLabel,
        risk_profile_id: preferences.risk_profile.risk_profile_id,
        synthetic: clientProfile?.synthetic ?? true
      },
      account_profile: {
        account_id: accountProfile?.account_id ?? 'acct_demo',
        client_id: accountProfile?.client_id ?? 'client_demo',
        account_type: accountProfile?.account_type ?? 'BROKERAGE',
        base_currency: accountProfile?.base_currency ?? 'USD',
        taxable: accountProfile?.taxable ?? true
      },
      portfolio_snapshot: {
        snapshot_id: `snap_${accountProfile?.account_id ?? 'acct_demo'}_${Date.now()}`,
        account_id: accountProfile?.account_id ?? 'acct_demo',
        as_of: asOf,
        holdings: [
          {
            instrument_id: 'inst_equity',
            symbol: 'EQUITY',
            asset_class: 'equity',
            quantity: '1',
            market_price: String(value.equityValue),
            market_value: String(value.equityValue),
            as_of: asOf
          },
          {
            instrument_id: 'inst_fixed_income',
            symbol: 'BONDS',
            asset_class: 'fixed_income',
            quantity: '1',
            market_price: String(value.fixedIncomeValue),
            market_value: String(value.fixedIncomeValue),
            as_of: asOf
          }
        ],
        cash: String(value.cashValue),
        total_value: String(totalValue)
      },
      allocation_target: {
        target_id: preferences.allocation_target.target_id,
        account_id: accountProfile?.account_id ?? 'acct_demo',
        asset_class_targets: preferences.allocation_target.asset_class_targets,
        tolerance_bands: preferences.allocation_target.tolerance_bands
      },
      risk_profile: {
        risk_profile_id: preferences.risk_profile.risk_profile_id,
        risk_level: preferences.risk_profile.risk_level,
        max_single_position_pct: preferences.risk_profile.max_single_position_pct,
        max_sector_pct: preferences.risk_profile.max_sector_pct,
        allowed_asset_classes: preferences.risk_profile.allowed_asset_classes
      }
    };
  }

  private startMarketStream(accountId?: string) {
    this.marketSubscription?.unsubscribe();
    this.marketSubscription = this.marketStreamService.stream(accountId).subscribe({
      next: (event) => {
        const nextEvent = this.applyForcedDriftIfNeeded(event, this.selectedAccountId());
        this.marketEvent.set(nextEvent);
        this.marketStreamError.set(null);
        if (!this.selectedPortfolioBalanced()) {
          this.syncFormToMarket(nextEvent);
        }
        this.maybeAutoGenerateRecommendation(this.selectedAccountId());
      },
      error: () => this.marketStreamError.set('Market simulation stream is not reachable yet.')
    });
  }

  private patchFormFromPortfolio(portfolio: PortfolioRecord) {
    const snapshot = portfolio.portfolio_snapshot;
    const equityValue = this.valueForAssetClass(snapshot.holdings, 'equity');
    const fixedIncomeValue = this.valueForAssetClass(snapshot.holdings, 'fixed_income');
    this.form.patchValue(
      {
        displayLabel: portfolio.client_profile.display_label,
        equityValue,
        fixedIncomeValue,
        cashValue: Number(snapshot.cash),
        targetEquityPct: Number(portfolio.allocation_target.asset_class_targets['equity'] ?? 60),
        targetFixedIncomePct: Number(
          portfolio.allocation_target.asset_class_targets['fixed_income'] ?? 30
        ),
        targetCashPct: Number(portfolio.allocation_target.asset_class_targets['cash'] ?? 10)
      },
      { emitEvent: false }
    );
  }

  private loadPreferencesForPortfolio(clientId: string) {
    console.log('Loading preferences for client:', clientId);
    this.preferenceService.getPreferences(clientId).subscribe({
      next: (preferences) => {
        console.log('Loaded preferences:', preferences);
        
        // Update target allocation fields with saved preferences
        const equityTarget = Number(preferences.allocation_target.asset_class_targets['equity']);
        const fixedIncomeTarget = Number(preferences.allocation_target.asset_class_targets['fixed_income']);
        const cashTarget = Number(preferences.allocation_target.asset_class_targets['cash']);
        
        console.log('Updating form with targets:', { equityTarget, fixedIncomeTarget, cashTarget });
        
        this.form.patchValue(
          {
            targetEquityPct: equityTarget,
            targetFixedIncomePct: fixedIncomeTarget,
            targetCashPct: cashTarget
          },
          { emitEvent: false }
        );
        
        console.log('Form values after patch:', this.form.value);
        
        // Reload portfolios to get updated risk profile
        this.portfolioService.list().subscribe({
          next: (portfolios) => {
            console.log('Reloaded portfolios:', portfolios);
            this.portfolios.set(portfolios);
          },
          error: (err) => {
            console.warn('Could not reload portfolios:', err);
          }
        });
      },
      error: (err) => {
        console.warn('Could not load preferences for client:', clientId, err);
        // Keep the default values from portfolio
      }
    });
  }

  private valueForAssetClass(
    holdings: PortfolioRebalanceRequest['portfolio_snapshot']['holdings'],
    assetClass: string
  ) {
    return holdings
      .filter((holding) => holding.asset_class === assetClass)
      .reduce((total, holding) => total + Number(holding.market_value), 0);
  }

  private syncFormToMarket(event: MarketStreamEvent) {
    const portfolioValue = Number(event.monitoring.portfolio_value);
    const equityPct = Number(event.monitoring.current_allocation['equity'] ?? 0);
    const fixedIncomePct = Number(event.monitoring.current_allocation['fixed_income'] ?? 0);
    const cashPct = Number(event.monitoring.current_allocation['cash'] ?? 0);

    console.log('Syncing form to market event. Current allocation:', { equityPct, fixedIncomePct, cashPct });

    this.form.patchValue(
      {
        equityValue: this.toCurrencyValue(portfolioValue, equityPct),
        fixedIncomeValue: this.toCurrencyValue(portfolioValue, fixedIncomePct),
        cashValue: this.toCurrencyValue(portfolioValue, cashPct)
      },
      { emitEvent: false }
    );
    
    console.log('Form values after sync (only position values should change, not targets):', this.form.value);
  }

  private toCurrencyValue(totalValue: number, percent: number) {
    return Number(((totalValue * percent) / 100).toFixed(2));
  }

  private toBalancedMarketEvent(event: MarketStreamEvent): MarketStreamEvent {
    const target = this.form.getRawValue();
    const balancedAllocation = {
      equity: String(target.targetEquityPct),
      fixed_income: String(target.targetFixedIncomePct),
      cash: String(target.targetCashPct)
    };

    return {
      ...event,
      monitoring: {
        ...event.monitoring,
        current_allocation: balancedAllocation,
        drift: {
          equity: '0.00',
          fixed_income: '0.00',
          cash: '0.00'
        },
        max_abs_drift_pct: '0.00'
      },
      rebalance: {
        signal: 'NO_ACTION',
        reason: 'Approved recommendation has been applied in the simulation.',
        threshold_pct: event.rebalance.threshold_pct
      }
    };
  }

  private isAccountBalanced(accountId: string): boolean {
    return this.balancedAccounts()[accountId] ?? false;
  }

  private setAccountBalanced(accountId: string, balanced: boolean) {
    this.balancedAccounts.update((current) => ({ ...current, [accountId]: balanced }));
  }

  private enableForcedDriftForAccount(accountId: string) {
    this.forcedDriftByAccount.update((current) => ({ ...current, [accountId]: true }));
  }

  private clearForcedDriftForAccount(accountId: string) {
    this.forcedDriftByAccount.update((current) => ({ ...current, [accountId]: false }));
  }

  private enableAutoRecommendForAccount(accountId: string) {
    this.autoRecommendByAccount.update((current) => ({ ...current, [accountId]: true }));
  }

  private clearAutoRecommendForAccount(accountId: string) {
    this.autoRecommendByAccount.update((current) => ({ ...current, [accountId]: false }));
  }

  private maybeAutoGenerateRecommendation(accountId: string) {
    if (this.submitting()) return;
    if (this.displayMarketEvent()?.rebalance.signal !== 'REBALANCE_NEEDED') return;

    const current = this.recommendation();

    if (current !== null) {
      const proposalStatus = current.recommendation_package?.proposal?.proposal_status;
      const approvalStatus = current.approval_artifact?.approval_status;

      // Stale: no-action while market says rebalance needed — clear and refresh
      if (proposalStatus === 'NO_ACTION_NEEDED' && approvalStatus === 'PENDING') {
        this.recommendation.set(null);
        this.approvalMessage.set(null);
        // Reset gate so we submit once for this new cycle
        this.hasAutoSubmittedByAccount.update(m => ({ ...m, [accountId]: false }));
        // Fall through to submit
      } else {
        // PENDING with trades, REJECTED, APPROVED, BLOCKED — leave alone
        return;
      }
    }

    // Gate: submit exactly once per drift cycle
    if (this.hasAutoSubmittedByAccount()[accountId]) return;
    this.hasAutoSubmittedByAccount.update(m => ({ ...m, [accountId]: true }));
    this.submitRebalance();
  }

  private applyForcedDriftIfNeeded(event: MarketStreamEvent, accountId: string): MarketStreamEvent {
    if (!(this.forcedDriftByAccount()[accountId] ?? false)) {
      return event;
    }

    return this.toForcedDriftEvent(event);
  }

  private toForcedDriftEvent(event: MarketStreamEvent): MarketStreamEvent {
    return {
      ...event,
      monitoring: {
        ...event.monitoring,
        current_allocation: {
          equity: '67.50',
          fixed_income: '24.50',
          cash: '8.00'
        },
        drift: {
          equity: '7.50',
          fixed_income: '-5.50',
          cash: '-2.00'
        },
        max_abs_drift_pct: '7.50'
      },
      rebalance: {
        ...event.rebalance,
        signal: 'REBALANCE_NEEDED',
        reason: 'Drift simulation intentionally pushed allocation beyond threshold.',
        threshold_pct: '5.00'
      }
    };
  }

  private updateApprovalStatus(status: string) {
    const current = this.recommendation();
    if (!current?.approval_artifact) {
      return;
    }
    this.recommendation.set({
      ...current,
      approval_artifact: {
        ...current.approval_artifact,
        approval_status: status
      }
    });
  }

  protected navigateToPreferences() {
    this.router.navigate(['/preferences']);
  }

  protected navigateToIntelligence() {
    this.router.navigate(['/intelligence']);
  }

  // ── Phase 3: Q&A Chat ──────────────────────────────────────────────────────

  private readonly http = inject(HttpClient);

  protected readonly chatMessages = signal<Array<{role: 'user' | 'assistant'; content: string}>>([]);
  protected readonly chatInput = signal('');
  protected readonly chatStreaming = signal(false);

  protected readonly suggestedQuestions = [
    'Why is equity being sold?',
    'What happens if I reject this?',
    'Explain the policy check',
    'What is the risk if I don\'t rebalance?',
  ];

  protected askQuestion(question: string) {
    const approvalId = this.recommendation()?.approval_artifact?.approval_id;
    if (!approvalId || this.chatStreaming()) return;

    this.chatMessages.update(msgs => [...msgs, { role: 'user', content: question }]);
    this.chatInput.set('');
    this.chatStreaming.set(true);

    // Add empty assistant message that will be filled by streaming
    this.chatMessages.update(msgs => [...msgs, { role: 'assistant', content: '' }]);

    // Scroll to bottom after adding messages
    setTimeout(() => this.scrollChatToBottom(), 50);

    const url = apiUrl(`/recommendations/${approvalId}/explain`);

    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, actor_id: 'local_owner' }),
    }).then(async (res) => {
      if (!res.ok || !res.body) {
        this.appendToLastMessage('Error: could not reach the explanation service.');
        this.chatStreaming.set(false);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === 'token') {
              this.appendToLastMessage(event.content);
            } else if (event.type === 'done') {
              this.chatStreaming.set(false);
            }
          } catch { /* ignore parse errors */ }
        }
      }
      this.chatStreaming.set(false);
    }).catch(() => {
      this.appendToLastMessage('Error: connection failed.');
      this.chatStreaming.set(false);
    });
  }

  protected submitChatInput() {
    const q = this.chatInput().trim();
    if (q) this.askQuestion(q);
  }

  private appendToLastMessage(chunk: string) {
    this.chatMessages.update(msgs => {
      if (!msgs.length) return msgs;
      const updated = [...msgs];
      updated[updated.length - 1] = {
        ...updated[updated.length - 1],
        content: updated[updated.length - 1].content + chunk,
      };
      return updated;
    });
    // Keep scrolled to bottom as tokens stream in
    this.scrollChatToBottom();
  }

  private scrollChatToBottom() {
    // Use requestAnimationFrame to scroll after Angular renders
    requestAnimationFrame(() => {
      const el = document.querySelector('.chat-messages');
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  protected clearChat() {
    this.chatMessages.set([]);
    this.chatInput.set('');
  }

  // ── Phase 3: What-If Simulator ─────────────────────────────────────────────

  protected readonly showSimulator = signal(false);
  protected readonly simulatorEquity = signal(60);
  protected readonly simulatorFixedIncome = signal(30);
  protected readonly simulatorCash = signal(10);
  protected readonly simulatorMaxPosition = signal(85);
  protected readonly simulatorResult = signal<{
    drift_changes: Array<{key: string; current_pct: string; target_pct: string; drift_pct: string; within_tolerance: boolean}>;
    policy_verdict: string;
    would_be_blocked: boolean;
    scenario_summary: string;
  } | null>(null);
  protected readonly simulatorLoading = signal(false);
  private simulatorDebounce: ReturnType<typeof setTimeout> | null = null;

  protected toggleSimulator() {
    this.showSimulator.update(v => !v);
    if (this.showSimulator()) {
      // Seed sliders from current recommendation
      const rec = this.recommendation();
      if (rec?.recommendation_package?.target_allocation) {
        const t = rec.recommendation_package.target_allocation;
        this.simulatorEquity.set(Number(t['equity'] ?? 60));
        this.simulatorFixedIncome.set(Number(t['fixed_income'] ?? 30));
        this.simulatorCash.set(Number(t['cash'] ?? 10));
      }
    }
  }

  protected onSimulatorChange() {
    if (this.simulatorDebounce) clearTimeout(this.simulatorDebounce);
    this.simulatorDebounce = setTimeout(() => this.runSimulation(), 500);
  }

  protected runSimulation() {
    const approvalId = this.recommendation()?.approval_artifact?.approval_id;
    if (!approvalId) return;

    this.simulatorLoading.set(true);
    const url = apiUrl(`/recommendations/${approvalId}/simulate`);

    this.http.post<any>(url, {
      scenario: {
        equity_target: this.simulatorEquity(),
        fixed_income_target: this.simulatorFixedIncome(),
        cash_target: this.simulatorCash(),
        max_single_position_pct: this.simulatorMaxPosition(),
      }
    }).subscribe({
      next: (result) => {
        this.simulatorResult.set(result);
        this.simulatorLoading.set(false);
      },
      error: () => this.simulatorLoading.set(false),
    });
  }

  protected applySimulatorValues() {
    this.form.patchValue({
      targetEquityPct: this.simulatorEquity(),
      targetFixedIncomePct: this.simulatorFixedIncome(),
      targetCashPct: this.simulatorCash(),
    }, { emitEvent: false });
    this.showSimulator.set(false);
  }
}
