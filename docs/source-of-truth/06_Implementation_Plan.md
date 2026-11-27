# 06 — Implementation Plan: Step-by-Step Build Sequence

**CCTV-AI-Agent | MVP v1 | 26 November 2026**  
**Current task boundary:** This ZIP is documentation-only. Do not start implementing merely because it is opened in Antigravity. The owner will explicitly authorize phases. The seven source-of-truth documents define the MVP; existing code is the baseline to adapt, not discard.

## 1. Agent instructions and delivery discipline

Read `00_Prototype_Comparison.pdf` and 01–06 before writing code. Inventory actual files and existing behavior, reconcile discrepancies, identify dependencies and external blockers. Present phase plan and expected file changes; implement only the authorized phase. Keep functioning features, reuse current stack, avoid scope expansion and hidden mock data. If a decision contradicts these docs, stop on that decision and ask owner; do not edit specs to match implementation. For each phase produce a concise report: changes by file, exact commands/tests and actual results, remaining gaps, security implications, **Your action required**, and proposed next phase. Mark PASS only for completed tests; distinguish local/mock, live provider and deployment tests. Do not expose secrets. Do not run destructive migrations, deploy, change provider accounts or push without approval. Commit only meaningful verified changes with clear messages; keep unrelated edits separate. Never claim a test or benchmark ran if it did not.

## 2. Baseline and estimated effort

Baseline source: uploaded `CCTV-AI-Agent.zip`; repo `https://github.com/hitmos81007/CCTV-AI-Agent`; frontend `https://cctv-ai-agent-frontend.vercel.app/`. Existing React, legacy static HTML, FastAPI, LangGraph, durable job worker, YOLO, PostgreSQL migrations and review endpoints are reusable. Comparison PDF establishes code-level gaps, not current deployed state. Previous audit reported 34 passing tests and 6 subtests; re-run in actual workspace and record exact environment. No new test execution or live credentials verification occurred during document packaging. Initial audit estimate 25–40 engineering hours with AI assistance is approximate, not a deadline or promise; staging, storage, GPU and hosting blockers may increase it. No need to rush into a rebuild.

## 3. Dependency-ordered phases

### Phase 1 — Baseline, workspace and consolidation (P0)

**Goal:** A reproducible single frontend and a clear supported backend entry point without breaking existing code.

- Open extracted project root. Inspect README, `.env.example`, requirements, migrations, `frontend/react/package.json`, existing Vercel config and tests. Confirm Git remote/history before any commit; original ZIP lacks `.git` metadata. Never force push or overwrite remote history. Use owner-approved existing clone/branch and copy docs if needed.
- Set up isolated Python environment and dependencies from existing requirements, `npm ci` in `frontend/react`, inventory model/runtime availability, run existing backend and browser/build tests. Record exact versions and failures. Do not download or upgrade models unnecessarily.
- Select `frontend/react` as official Vercel root and correct effective build config to React `dist` + SPA fallback. Local build should not copy `review.html`. Retain legacy UI and workflows for reference until replacement validated. Map official LangGraph plan/worker path and legacy endpoints to deactivate later; do not create a second planner.
- Update environment example placeholders and setup notes (no secret values), add explicit runtime mode guard design. Identify existing local data path; do not touch user records.

**Done gate:** Baseline test results recorded, React production build succeeds, routes/build root documented, no app logic regression, owner confirms correct Git history. If environment missing, record blocked checks and don't call gate PASS. Suggested commit `chore: establish React MVP build baseline`.

**Your action required:** Confirm existing repository/branch, local Python/Node and GPU availability, allow installation if needed. No need to share secrets in chat.

### Phase 2 — Supabase database, migrations and media security (P0)

**Goal:** Durable data foundation and owner-safe schema before user-facing auth or uploads.

- Inspect dedicated Supabase schema and applied migration versions with authorized access. Back up and use staging/test project first. Compare PostgreSQL schema to runtime initialization in `app/db.py`. Keep IDs and historical integrity; no destructive reset.
- Add sequential migrations starting 004 implementing doc 05: owner fields, status/run constraints, upload/start/review idempotency, job heartbeat and candidate uniqueness, review events, nullable unknown capture timestamps, indexes, same-case integrity, minimal `case_events`, RLS and restricted grants. Quarantine unowned legacy rows; no inferred ownership. Update SQLite test fixture schema as necessary. No production SQLite fallback.
- Set up private evidence bucket/policy; use backend-managed durable raw volume default; prove media access control and persistence. Prepare environment validation. Realtime publish only minimal sanitized events; no job tokens or raw server paths.
- Migration tests on empty and populated copy; verify legacy data retention, constraints, direct RLS checks with test users, safe publication, private bucket access. Record SQL deployment and recovery instructions; actual production application only after approval.

**Done gate:** Staging migrations successfully applied and reapplicable safely, owner RLS tests fail closed, no legacy rows leak, storage private and accessible to authorized backend only. Unit tests pass. If no live Supabase credentials/project, explicitly mark live gate blocked (mock tests insufficient). Suggested commit `feat: add owner-scoped Supabase schema and event channel`.

**Your action required:** Create/confirm dedicated Supabase project, staging if possible; configure URL, public key, database connection and backend-only Storage credential in secure local env; approve backup, retention and storage limits; supply explicit mapping only if old data must be accessible. Do not send passwords to frontend or commit.

### Phase 3 — Authentication, Officer Verification, District Administration & Premium Frontend (P0)

**Goal:** Replace shared credentials with Supabase Auth, multi-step police officer registration & credential verification, district-scoped administrative approval/suspension, and a premium dark frontend for all auth/onboarding/admin/approved-shell screens.

- Add Supabase JS client and auth provider in React; officer self-registration (Account → immediate session → Professional Details → Supporting Document Upload), forgot/reset password, approval-status screen, protected routes, session restoration, logout and cache/subscription cleanup.
- Add forward-only PostgreSQL migration `005` (`districts`, `profiles`, `district_admins`, `verification_documents`, `verification_reviews`, `verification_events`, plus Phase 2 audit same-case `recordings` and `orchestration_tasks` constraints) and private `officer-verification` Storage bucket.
- Implement backend bearer JWT verification (JWKS + signature/issuer/audience/expiry/sub checks) with separate authorization dependencies (authenticated user, approved officer, active district admin, owned case, district-scoped admin action). Suspend/revoke takes effect immediately on subsequent backend requests. Disable insecure legacy routes (`/api/v1/zones/analyze`, `/api/v1/zone/analyze`, ONVIF/NVR, global `/api/v1/audit`, unrestricted camera registration) and CORS-linked Basic Auth bypass.
- Build District Administrator shell and verification workspace with real database-backed counts, private document previews, confirmation dialogs with mandatory reasons, and server-side bootstrap provisioning script (`scripts/provision_admin.py`).

**Done gate:** Two real officer accounts self-registered and tested end-to-end (pending denial, admin approval, two-officer isolation, rejection/suspension/reinstatement); admin cross-district and self-approval blocked; private `officer-verification` bucket verified; TypeScript, React build, Playwright, and backend security tests pass. Suggested commits separating specs, DB/storage, auth/RBAC, frontend, and tests.

**Your action required:** Configure Supabase Email/Password Auth (self-registration enabled, mandatory email confirmation disabled for hackathon MVP to avoid SMTP limits, site/redirect URLs configured for password recovery), provision initial District Administrator via `scripts/provision_admin.py`, and test two real officer accounts through the frontend. Do not provide actual passwords in prompts.

### Phase 4 — Core feature 1: Case creation and multi-video ingestion (P0)

**Goal:** End user can create and upload a batch from React with no internal configuration.

- Simplify existing case creation API/schema and page; server-generated ID/internal zone, description/title/authorisation, no fabricated capture time. Create draft once and allow reopen. Preserve required legacy DB fields via safe defaults.
- Reuse `POST /api/v1/videos` and ingestion checks. Stream size cap, safe filename handling, validation of format/decode/duration, hashes, atomic DB ready registration and staged file cleanup. Add upload idempotency, draft-only deletion, owner-safe list/read APIs. Validate same-case source IDs. Ensure durable local raw volume and private media path policy.
- Build file drop zone, multi-select, preview, metadata, byte progress, cancellation/retry/removal and per-file failure handling. Fetch ready list after reload. Start disabled until all retained selections ready and at least one file. No analysis automatically triggered by upload.

**Done gate:** Three or more authorised small fixture videos uploaded; records match physical files/hashes; corrupt/oversize/unsupported and interrupted uploads do not register ready; duplicate retry no new video; account B blocked; refresh recovers draft. Existing backend tests adapted, React build passes. Suggested commit `feat: connect authenticated batch upload workflow`.

**Your action required:** Supply small staged/synthetic clips containing known common objects/vehicles and negatives; approve size/count/retention defaults, verify disk capacity. No real personal tracking dataset required.

### Phase 5 — Core feature 2: LangGraph and durable agent execution (P0)

**Goal:** Exactly one official path delegates genuine video processing to workers and finalizes runs.

- Adapt `app/orchestration.py`: validate supported non-identifying object clue from local Ollama; save parsed clue, reserve one run with stable key, snapshot ready videos, atomically queue one task/job each. No zero-job success, no premature follow-up planning, no cross-camera identity work. Keep `/plan` as entry. Lock inputs after reservation; reconcile planning crashes and safe retry if no jobs exist.
- Adapt `app/worker.py` and `app/video_analysis.py`: unique process identity, atomic claims, bounded workers, heartbeats, lease fencing, retry/backoff, deterministic deduplication and durable frame storage. Update terminal job and case aggregation in transaction; reconcile on restart. Review should not create followups. Verify audit-chain concurrency. Never present heuristic clue-match score as probability.
- Reuse actual YOLO detection; record source ID, video offset, model confidence, frame hash and known recorded time only. Store private evidence frames; no external frame upload to LLM. Test positive/negative clips, known/unknown timestamps, Ollama failure, worker failure, lease expiry, stale writes, duplicate plan, job retry and partial completion.
- Test concurrency with two real worker identities in controlled run; record overlapping job intervals and actual inference behavior. Start with hardware-safe worker count and benchmark; do not overload GPU. Report actual time-to-first finding and total time, versions and fixture sizes; no invented performance.

**Done gate:** One start key → one run → one job per ready video; actual detections from fixtures; duplicate start safe; worker failure recovers without duplicated findings; correct completed/partial/failed status; concurrency verified or explicitly blocked by hardware. Suggested commit `feat: complete durable LangGraph worker lifecycle`.

**Your action required:** Start local Ollama with installed compatible model and approve hardware worker count; provide host/GPU info. If using cloud compute approve cost and setup. Do not claim parallel GPU execution until measured.

### Phase 6 — Core feature 3: Realtime, candidate review and persistent history (P0)

**Goal:** Real findings and progress arrive in UI and survive browser/backend restart.

- Emit minimal `case_events` transactionally from real DB changes; frontend subscribes scoped to owner/case and invalidates query keys. Re-fetch after initial subscription and reconnect; polling fallback; cleanup on unmount/logout. Verify live changes with real Supabase, not simulated events.
- Integrate activity progress, actual worker/source status, candidate cards, evidence drawer, source metadata and optional playback when retained. Display honest no-result/partial/error states. Remove map, reviewer name field and raw JSON UI.
- Review endpoint appends authenticated event with idempotency, updates status and audit in one transaction; no automated investigation side effect. History query shows owner cases and review decisions, supports reload and revised decision history. Cache invalidation for affected views.

**Done gate:** Candidate appears in browser without manual refresh; reconnect restores events; review persists after reload/relogin/backend restart; A/B Realtime isolation and private evidence verified; no artificial progress; no duplicate review event on retry. Suggested commit `feat: stream case updates and persist human review history`.

**Your action required:** Verify Realtime publication and access in Supabase; inspect real fixture findings and review results; approve media retention choices.

### Phase 7 — UI polish and responsive design (P1 after core)

**Goal:** Make working flows attractive, clear and accessible, without new product features.

- Apply doc 04 tokens, shared AppShell and components; dashboard, new case, workspace, history and auth pages share visual language. Ensure actual metrics only. Add distinct loading, empty, error, partial, reconnect and disabled states; remove dead legacy routes after replacement passes. Keep existing working React abstractions.
- Keyboard and screen-reader support, contrast checks, focus trap for dialogs, reduced motion, responsive 375/768/1024/1440px layouts, zoom, long filenames and note handling. No maps, simulated dashboards, generic stock evidence or unrequested functionality.

**Done gate:** Screenshot evidence at desktop/mobile, keyboard and contrast checks, no broken routes/overflow, all core tests still pass. Suggested commit `style: polish responsive investigation and review experience`.

**Your action required:** Review screenshots and approve appearance before final deployment. UI preferences can be revised explicitly in source-of-truth docs.

### Phase 8 — Testing, reliability and security regression (P0 release gate)

**Goal:** Repeatable objective evidence that the full MVP works, not just individual modules.

Run existing and new Python tests, frontend type/build tests, Playwright flows, isolated Postgres migrations, staging Supabase integration, genuine video inference and real browser integration. Test owner A/B, anonymous auth, invalid IDs, stale session, malformed input, corrupt/oversize uploads, duplicated requests, missing frame, disconnected Realtime, stale planner/worker, partial failure, zero matches, review history, browser closure and process restart. Test runtime fail-closed without DB/provider config. Keep sensitive info out of test output. Measure actual resource usage and timings, and document any deviations from scope. Do not claim actual provider/GPU tests from mocked unit tests.

**Done gate:** All PRD A01–A10 locally/staging where applicable; evidence table below populated with PASS/FAIL/BLOCKED and command/log reference; fix critical issues before deployment. Deployment-specific A10 remains pending until Phase 9. Suggested commit `test: verify complete authenticated investigation workflow`.

**Your action required:** Run/observe hardware-dependent tests when requested, approve safe staged footage, confirm final UI and report actual behavior from your machine if agent cannot access hardware.

### Phase 9 — Deployment and reproducible demo (P0 release gate)

**Goal:** Publicly reachable frontend with authenticated access to persistent backend and Supabase, not public evidence.

- With approval, configure Vercel React root/build/dist, SPA fallback and public env; verify Auth URLs. Provision or expose secure HTTPS backend on persistent host, exact CORS origins, production DB connection, private media storage, supervised worker pool and health checks. Do not expose local Ollama or database directly. Keep tokens server-side. Check upload transport limits and timeout; do not use Vercel serverless for processing.
- Apply approved migrations to target project after backup and verify schema/RLS/publication. Run genuine deployed end-to-end demo with two invited accounts; verify direct deep links, upload, real jobs, incoming findings, review, history after refresh/re-login, worker restart, isolation and private media. Document measured demo resources and times, not PPT benchmark claims. Provide setup/run instructions, known constraints and rollback steps. Preserve previous deployment/backup for rollback.

**Done gate:** Deployed browser passes all A01–A10 with evidence; no critical security/functional blocker. If backend hosting or Supabase live integration not available, report exactly what is blocked; local demo alone is not deployed completion. Suggested commit `chore: document and verify MVP deployment` after approved changes.

**Your action required:** Approve actual host, HTTPS/domain, Vercel project changes, production env/migrations and costs, then authorize deployment/push. Keep credentials in provider dashboards/local secret store. Confirm final demo access and retention policy.

## 4. Verification register (populate during implementation, not now)

| Test ID | Required evidence | Initial status |
|---|---|---|
| A01 | Real Supabase login/reset/session/logout and token checks | NOT RUN |
| A02 | Batch staged uploads and rejection of corrupt file | NOT RUN |
| A03 | Idempotent start and exact job count | NOT RUN |
| A04 | Distinct worker IDs and overlap; measured inference | NOT RUN |
| A05 | Real positive/negative fixture findings with provenance | NOT RUN |
| A06 | Real Realtime update + disconnect/reconnect | NOT RUN |
| A07 | Authenticated review/history persistence across restart | NOT RUN |
| A08 | Two-account API/RLS/Realtime/media negative tests | NOT RUN |
| A09 | Worker restart/lease/retry/terminal aggregation | NOT RUN |
| A10 | Deployed browser complete flow, build and HTTPS | NOT RUN |

Record command, test fixture, environment (mock/local/staging/deployed), expected/actual result, date and failure details. Maintain progress outside these seven documents (e.g. `docs/implementation-progress.md` during approved implementation). Never modify source-of-truth documents without owner approval. Core product complete only after all required acceptance tests pass; optional features are not release blockers. No extra enterprise functionality or new service merely to improve perceived completeness.

## 5. First Antigravity instruction (copy only after opening workspace)

> Read all seven files in `docs/source-of-truth/`, including the original comparison PDF. Inspect the existing repository and reconcile the source snapshot with the requirements. Do not write, modify, delete, migrate, deploy, push, or execute an implementation phase yet. Summarize the MVP, current baseline, major gaps, required external setup and a proposed Phase 1 change list. Identify any conflicting requirements or assumptions. Wait for my explicit instruction before implementing Phase 1. Use these seven documents as the source of truth for the entire project; keep the scope limited to authenticated batch upload, real agent analysis, candidate review and saved history.
