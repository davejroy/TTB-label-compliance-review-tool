# Frontend/backend architecture review

Reviewed against the prod repository after the Render health-check and reproducible-build update.

## Executive summary

The codebase is usable and already has meaningful test coverage, but it is not yet organized as a layered application. The boundaries are present informally:

- Pydantic models define API contracts.
- \`compliance.py\` contains mostly pure business rules.
- \`claude_client.py\` is the external AI provider boundary.
- React components provide a workable UI split.

The primary maintainability risk is that several modules have become application-sized controllers. The next refactor should preserve behavior and tests while moving orchestration behind explicit services and ports.

## Backend findings

- \`backend/app/main.py\` is approximately 1,155 lines. It combines application construction, CORS/configuration, request validation, image handling, provider retry behavior, route handlers, streaming, and batch orchestration.
- \`backend/app/compliance.py\` is approximately 1,586 lines. Keeping its core rules pure is a strong design choice, but the module should be split by domain responsibility: normalization, field policies, warnings, aggregation, and serialization.
- \`models.py\` is a good API/schema boundary. Keep transport schemas separate from domain objects as the application grows.
- \`claude_client.py\` is a useful provider boundary, but a singleton makes provider substitution and isolated tests harder. Introduce an \`ExtractionProvider\` protocol and an \`AnthropicExtractionProvider\` adapter behind dependency injection.
- \`auth.py\` is explicitly demo-grade. It must not be treated as production authentication or authorization.
- CORS parsing should fail closed for production. An empty or malformed production allow-list should be a startup/configuration error, not a silent wildcard fallback.

## Frontend findings

- \`App.tsx\` is appropriately small, but \`SingleReview.tsx\`, \`BatchReview.tsx\`, and \`LabelOnlyCheck.tsx\` carry too much state and async orchestration.
- \`api.ts\` is a large mixed client. Split it into bounded clients for review, label checks, COLA, streaming, and normalized errors.
- Extract hooks such as \`useSingleReview\`, \`useBatchReview\`, \`useLabelCheck\`, and \`useStreamingBatch\`. Keep presentational result panels free of request lifecycle logic.
- Use discriminated TypeScript event/result types for NDJSON. Centralize HTTP, timeout, retry, and user-facing error translation.
- Session storage of a demo token is acceptable only for the demo mode already documented in the code; production should use a real identity/session design.

## Recommended OOP boundary

Do not convert every compliance rule into a class. Keep deterministic rules as pure functions. Use small classes/protocols at side-effect boundaries:

\`\`\`text
routers -> application services -> domain functions
                         |-> ExtractionProvider
                         |-> ImageStore / audit sink
                         |-> clock and configuration ports
\`\`\`

Suggested target modules:

\`\`\`text
backend/app/
  api/routers/{health,review,label_check,cola}.py
  application/{review_service,batch_service,label_check_service}.py
  domain/{compliance,normalization,aggregation}.py
  ports/{extraction_provider,audit_sink,image_store}.py
  infrastructure/{anthropic_provider,logging,settings}.py
  schemas/{requests,responses,events}.py
\`\`\`

This produces testable seams without forcing object-oriented wrappers around pure policy code.

## Staged refactor plan

1. Add typed settings and a fail-closed CORS policy.
2. Extract router modules without changing endpoint contracts.
3. Introduce \`ReviewService\` and \`BatchReviewService\`; keep current route behavior as the regression oracle.
4. Split \`compliance.py\` into pure domain modules.
5. Replace the provider singleton with an injected protocol and test double.
6. Extract frontend hooks and API clients.
7. Add parity tests that run against both dev and production source trees, or consolidate shared application code into one package.

Each stage should land with unit tests, endpoint tests, and a small diff. Do not mix a behavior change with a structural move unless the change has a dedicated regression test.
