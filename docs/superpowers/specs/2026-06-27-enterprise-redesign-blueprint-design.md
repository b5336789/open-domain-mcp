# OpenDomainMCP Enterprise Redesign Blueprint

Date: 2026-06-27

## Executive Summary

OpenDomainMCP should be redesigned as an internal enterprise knowledge platform for agent grounding. The current system already has strong primitives: ingestion, review, synthesized articles, graph exploration, advisor, simulator, metrics, role-specific MCP views, and a Task Center. The primary gap is not feature absence; it is that these capabilities are exposed as separate tools instead of one governed lifecycle.

The recommended direction is a hybrid of:

- **Workflow-First Console**: make the product understandable through a clear operator lifecycle.
- **Quality Governance Platform**: make MCP publication depend on measurable knowledge-quality evidence.

The redesigned operating model is:

```text
Intake -> Extract -> Review -> Validate -> Publish -> Monitor
```

The 8-12 week redesign should preserve the existing Python/FastAPI, React/Vite, Chroma, MariaDB graph, ingestion, retrieval, RAG, article, graph, advisor, and MCP-view foundations. It should reorganize product workflows, introduce readiness and quality-evidence services, harden the job domain, and make MCP publication auditable.

This document is a blueprint and technical assessment only. It does not authorize product-code changes or an implementation plan.

## Scope And Constraints

### Target Product Type

OpenDomainMCP is treated as an **enterprise internal platform**, not a local-only utility and not an external SaaS product.

### Primary Success Criteria

The redesign prioritizes:

- User workflow: source intake, review, validation, publish, and monitoring should feel like one guided lifecycle.
- Knowledge quality: review state, article quality, graph health, RAG grounding, evals, and simulator scenarios should drive publish decisions.

### Primary Users

The platform should support three groups through one end-to-end loop:

- Knowledge/Product Ops: source intake, review, curation, article quality, MCP publish requests.
- Engineering/Platform Team: extraction quality, model/retrieval settings, graph health, deployment, job reliability.
- Agent/Automation Owners: MCP/Advisor/Simulator validation before agents execute product workflows.

### Technical Boundary

Use a conservative platform redesign:

- Keep the current core stack.
- Replace or harden weak seams only when they directly improve workflow, quality, or publish governance.
- Do not pursue a broad rewrite or microservice split in the first 8-12 weeks.

## Current System Assessment

### Strengths

- The core architecture has a single runtime composition point in `src/opendomainmcp/context.py`.
- The backend has broad test coverage. Verified baseline: `.venv/bin/python -m pytest -q` passed with `421 passed, 3 skipped`.
- The frontend production build is healthy. Verified baseline: `npm run build` completed successfully.
- The product already includes differentiated enterprise primitives:
  - hybrid dense + lexical retrieval
  - cited RAG
  - synthesized articles
  - entity/workflow/dependency graph
  - role-specific MCP views
  - Advisor
  - Simulator
  - metrics
  - review workflow
  - collections
  - Task Center

### Enterprise Gaps

1. **Workflow fragmentation**

   The web console currently presents many pages: Dashboard, Ingest, Explore, Ask, Browse/Edit, Articles, Review, Graph, Advisor, MCP Builder, Simulator, Metrics, and Settings. These are useful primitives, but they read as a feature list rather than a governed platform workflow.

2. **No explicit MCP readiness decision**

   Review state, metrics, evals, simulator results, graph health, and article quality exist separately. They should be combined into a single readiness decision for each knowledge base and MCP view.

3. **Large coordination hotspots**

   The following files carry too much orchestration responsibility:

   - `src/opendomainmcp/api/app.py`
   - `web/src/api.ts`
   - `web/src/App.tsx`

   This makes governance, lifecycle state, role policy, and quality evidence harder to keep consistent.

4. **Task execution is still prototype-grade for enterprise workflows**

   Task Center is the correct product direction, but the current implementation is an in-process worker with a file-backed task store. The first enterprise step should be a stable job domain and recovery model. A formal queue can be considered after the product workflow and job semantics are clear.

5. **Documentation and implementation diverge**

   Some docs still describe a graph fallback path through `NullGraphStore`, while `build_context()` currently requires MariaDB graph connectivity and fails loudly if the graph store is unavailable. This gap affects deployment trust.

6. **Development workflow has stability warnings**

   - `.venv/bin/python -m pytest -q` passes, but `.venv/bin/pytest` fails during collection because `tests/test_graph_collection_scope.py` imports `tests.conftest` while `tests` is not reliably importable through that launcher.
   - `npm run test:e2e` initially hit sandbox binding restrictions. With normal local server permissions it ran but all 10 specs failed through one layout crash: `Cannot read properties of undefined (reading 'filter')`. The immediate root cause is the Task Center expecting `/api/tasks` to return `{ tasks: [...] }` while the E2E mock layer returns `{}` for unmocked endpoints.

These issues are not product-direction blockers, but they should be fixed in Wave 1 because they reduce confidence in platform evolution.

## Target Operating Model

The platform should guide users through a closed lifecycle:

1. **Intake**
   Register sources, identify ownership, define scope, and launch ingestion or sync tasks.

2. **Extract**
   Run ingestion, extraction, embedding, graph sync, and article synthesis as jobs with visible progress and failure records.

3. **Review**
   Curate pending chunks, low-confidence extractions, rejected knowledge, metadata edits, and article drafts from one review queue.

4. **Validate**
   Run retrieval checks, RAG evals, graph-health checks, Advisor checks, and simulator scenarios.

5. **Publish**
   Publish MCP views only after readiness gates pass or a responsible owner records an override.

6. **Monitor**
   Track usage, grounding quality, stale sources, failed jobs, graph drift, and deprecated endpoints.

## Product Information Architecture

Replace the current feature-list navigation with five enterprise workspaces plus Settings.

### 1. Command Center

Command Center replaces the current Dashboard as the primary decision view.

It should show:

- active knowledge base
- lifecycle state
- readiness score
- blockers
- source health
- pending review count
- failed/stale jobs
- graph health
- MCP publish status
- next recommended action

### 2. Source Intake

Source Intake combines:

- server-path ingestion
- upload
- source registry
- sync/prune
- background task launch
- source delete
- source health

The user goal is: "Get product knowledge into the platform safely and visibly."

### 3. Knowledge Review

Knowledge Review combines:

- Browse/Edit
- Review
- Articles
- low-confidence extractions
- pending/rejected queues
- article curation
- source evidence side panel

The user goal is: "Turn extracted content into approved, trustworthy knowledge."

### 4. Quality Lab

Quality Lab combines:

- Explore
- Ask
- Graph
- Advisor
- Metrics
- evals
- simulator scenario results

The user goal is: "Prove this knowledge base grounds agents well enough to publish."

### 5. MCP Publish

MCP Publish combines:

- MCP Builder
- Simulator for publish scenarios
- endpoint state
- retrieval policy
- RBAC view policy
- readiness gates
- approval and override records

The user goal is: "Publish a role-specific MCP view with evidence."

### 6. Settings

Settings should remain, but should be grouped by operational intent:

- Workspace settings
- Retrieval/model settings
- Publish/security settings
- System/deployment diagnostics

## Target Technical Architecture

The target architecture keeps the current runtime core but moves orchestration out of oversized API and frontend coordination files.

### Backend Boundaries

Recommended backend modules:

- `api/workspace_routes.py`
  - knowledge-base overview
  - readiness summary
  - lifecycle state

- `api/intake_routes.py`
  - source intake
  - upload
  - ingest/sync job launch
  - source health

- `api/review_routes.py`
  - review queues
  - item edits
  - article curation
  - approval/rejection

- `api/quality_routes.py`
  - quality evidence
  - eval runs
  - graph health
  - simulator scenario records
  - grounding/citation metrics

- `api/publish_routes.py`
  - MCP publish lifecycle
  - endpoint status
  - readiness gate checks
  - decision records

- `api/job_routes.py`
  - job creation
  - job status
  - child records
  - cancellation
  - recovery

### Domain Services

Add service modules that own product decisions instead of embedding them in API handlers.

#### Readiness Service

Computes whether a knowledge base or MCP view is ready to publish.

Inputs:

- source coverage
- failed/skipped ingestion records
- approved/pending/rejected counts
- article coverage and article review status
- eval pass rate
- simulator scenario pass rate
- retrieval score health
- citation coverage
- graph health
- stale-source warnings
- failed jobs

Output:

- readiness status: `blocked`, `needs_review`, `validating`, `ready`, `published`
- score
- blockers
- warnings
- next recommended action

#### Quality Evidence Service

Unifies evidence from:

- metrics
- evals
- simulator scenarios
- search/ask outcomes
- graph health
- article synthesis and critique results
- source health

This service should produce evidence that can be attached to a publish decision.

#### Publish Governance Service

Owns MCP view lifecycle:

```text
draft -> validating -> approved -> published -> deprecated
```

It should enforce gates, record overrides, and make endpoint state auditable.

#### Job Domain Service

Owns job semantics for:

- ingest
- synthesize
- re-extract
- eval-run
- publish-validation

It should stabilize status values, result schemas, cancellation, recovery, and failure reporting before any queue replacement decision.

### Data Model Additions

These are conceptual models for the blueprint stage.

#### KnowledgeBaseReadiness

Fields:

- collection
- status
- score
- blockers
- warnings
- source_health
- review_health
- retrieval_health
- graph_health
- article_health
- job_health
- updated_at

#### QualityEvidence

Fields:

- collection
- view
- evidence_type
- status
- score
- input_refs
- output_refs
- summary
- created_at

#### PublishDecision

Fields:

- collection
- view
- version
- status
- gates
- evidence_refs
- approved_by
- approved_at
- override_reason
- endpoint_url

#### JobRun

Fields:

- id
- type
- collection
- status
- params
- total
- done
- failures
- result
- created_at
- started_at
- finished_at
- cancel_requested

## Knowledge Quality And MCP Governance

MCP publication should be governed by readiness gates.

### Gate 1: Coverage

Checks:

- source count
- source ownership
- indexed chunks
- skipped files
- failed files
- stale/deleted sources
- sync status

### Gate 2: Review

Checks:

- approved ratio
- pending count
- rejected count
- low-confidence count
- article draft status
- metadata completeness

### Gate 3: Retrieval

Checks:

- grounding hit rate
- average score
- retrieval precision
- minimum-score failures
- citation coverage
- approved-only retrieval behavior

### Gate 4: Graph And Workflow

Checks:

- entity count
- workflow count
- dependency edge health
- orphan graph records
- graph sync failures
- Advisor dependency/workflow support

### Gate 5: Simulation

Checks:

- scenario suite pass rate
- tool hit coverage
- grounding evidence per scenario
- regressions from previous version

### Gate 6: Policy

Checks:

- search mode
- rerank setting
- approved-only setting
- API key view scope
- endpoint state
- owner approval

## 8-12 Week Roadmap

### Wave 1: Weeks 1-3, Workflow IA And Command Center

Goal: make the platform understandable and operationally coherent.

Deliverables:

- Command Center
- Source Intake workspace
- lifecycle state model
- source health summary
- Task Center summary integration
- frontend shell reorganized into workspaces
- E2E layout crash fixed
- pytest launcher mismatch fixed or documented with a single blessed command

### Wave 2: Weeks 4-7, Quality Lab And Readiness Gates

Goal: make knowledge quality measurable.

Deliverables:

- Readiness Service
- Quality Evidence model
- Review Queue plus Article Curation workflow
- eval-run records
- simulator scenario suite
- grounding, citation, and coverage metrics
- graph health summary
- Quality Lab workspace

### Wave 3: Weeks 8-12, Publish Governance And Job Reliability

Goal: make MCP publication auditable and repeatable.

Deliverables:

- MCP publish lifecycle
- publish decision records
- readiness gates and override reasons
- endpoint version/status
- job result schemas
- job recovery and cancellation hardening
- queue replacement decision based on deployment scale, not assumption

## Success Metrics

### Product Workflow

- Time from source intake to first publishable MCP view decreases.
- Operators can identify the next required action from Command Center.
- Review backlog and stale-source counts are visible without drilling through multiple pages.

### Knowledge Quality

- Each published MCP view has an attached readiness record.
- Each published view has at least one simulator scenario suite result.
- Eval pass rate and grounding hit rate are visible per knowledge base.
- Article coverage and graph health are visible per knowledge base.

### Platform Reliability

- Failed jobs are recoverable or actionable.
- Publish decisions are auditable.
- E2E tests pass in the normal local development path.
- Backend test command is stable and documented.

## Risks And Mitigations

### Risk: Overbuilding Governance Before UX Is Usable

Mitigation: Wave 1 focuses on Command Center and Source Intake first. Quality gates become visible summaries before they become strict blockers.

### Risk: Readiness Score Becomes A Black Box

Mitigation: readiness output must include blockers, warnings, and evidence links. The score is secondary to explainable gates.

### Risk: Job Queue Replacement Distracts From Product Flow

Mitigation: define the Job Domain first. Replace the execution backend only if multi-process deployment, concurrency, or reliability requirements exceed the hardened file-backed worker.

### Risk: Existing Pages Become Hidden Instead Of Redesigned

Mitigation: each existing page must map into a new workspace workflow. No capability should disappear without an explicit replacement path.

### Risk: Documentation Diverges Again

Mitigation: architecture docs should state the actual graph-store dependency posture and the supported test commands after Wave 1.

## Non-Goals For The First 8-12 Weeks

- External SaaS billing.
- Full tenant administration UI.
- Microservice split.
- Replacing Chroma by default.
- Rewriting the ingestion pipeline.
- Rewriting the React application from scratch.
- Building a general-purpose analytics platform unrelated to MCP readiness.

## Recommended First Decisions

1. Approve the new workspace information architecture.
2. Treat Command Center readiness as the top product surface.
3. Introduce readiness and quality evidence as domain services before changing storage backends.
4. Keep Task Center, but stabilize job semantics and failure reporting.
5. Make MCP publishing a governed lifecycle rather than a direct endpoint toggle.
