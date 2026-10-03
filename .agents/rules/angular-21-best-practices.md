---
description: Angular 21+ frontend best practices for this project
globs: **/*.{ts,html,scss,css}
alwaysApply: false
---

# Angular 21+ Best Practices

- Prefer standalone components, standalone routes, and feature-level providers. Do not introduce NgModules unless integrating legacy Angular code.
- Use Angular signals for local reactive UI state. Use RxJS for streams, HTTP, websocket-like flows, and cancellation-heavy async work.
- Prefer the modern template control flow syntax (`@if`, `@for`, `@switch`) over structural directives for new templates.
- Keep components thin: UI composition in components, data access in services, request/response DTOs in typed models.
- Use typed reactive forms for complex intake workflows; keep validation rules explicit and testable.
- Use `ChangeDetectionStrategy.OnPush` for components unless there is a clear reason not to.
- Keep API calls behind typed Angular services. Components should not construct raw backend URLs or parse provider-specific payloads.
- Never expose raw chain-of-thought, internal traces, credentials, or sensitive personal data in the UI.
- Model workflow states explicitly (`NORMAL`, `DEGRADED`, `LOW_CONFIDENCE`, `BLOCKED`) and disable approval actions for blocked states.
- Add focused component/service tests for workflow state rendering, form validation, API error handling, and approval controls.

```typescript
// Prefer typed services and DTOs.
readonly recommendation = signal<RecommendationPackage | null>(null);

load(request: PortfolioRebalanceRequest) {
  return this.orchestrator.submit(request).pipe(
    tap((response) => this.recommendation.set(response.recommendation_package))
  );
}
```
