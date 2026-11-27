# 01 — Product Requirements Document (PRD)

**CCTV-AI-Agent | MVP v1 | 26 November 2026**  
**Tagline:** Upload footage. Coordinate analysis. Review candidate clues.  
**Audience:** Antigravity coding agent and project owner, Team Deadlock Defiers.

## 1. Source of truth and operating rules

These seven files in `docs/source-of-truth/` are the project specification, not evidence that features are already implemented. Read all seven and inspect the current repository before changing anything.

| File | Authority |
|---|---|
| `00_Prototype_Comparison.pdf` | Original requirements and baseline gap analysis; historical snapshot, not a live test report |
| `01_PRD.md` | MVP scope, features and acceptance |
| `02_TRD.md` | Stack, runtime, interfaces and engineering constraints |
| `03_App_Flow.md` | Routes, actions and state transitions |
| `04_UI_UX_Design_Brief.md` | Visual and interaction design |
| `05_Backend_Schema.md` | Data contracts, authorization and migration design |
| `06_Implementation_Plan.md` | Dependency-ordered implementation and verification gates |

The user's simplified MVP overrides broader aspirations in the older hackathon presentation and legacy README/workflows. The comparison PDF remains unchanged; the six specifications operationalize its requirements. Where it says a feature is recommended or optional, this PRD marks what is required for this build. If code and specs disagree, treat code as baseline and specs as target; if specifications conflict or an external decision is needed, report it and request approval rather than inventing scope. Do not treat template examples as requirements. Do not silently rewrite working modules. Do not make implementation changes merely because these documents exist: wait for the owner's phase instruction.

## 2. Product problem and intended user

Manually reviewing many authorised video recordings is slow. Relevant objects or vehicles can be missed, and the original footage, detected frames and review decisions can become scattered. This prototype consolidates that workflow in one web application. Its value is the coordinated distribution of analysis tasks, source-linked findings and an explicit human review loop, not a guarantee of finding every object.

Primary persona: an authorised evidence reviewer testing a bounded collection of recordings. They need a straightforward interface, can describe visible non-identifying object or vehicle attributes, and want to inspect original evidence before marking candidate relevance. They should not need to configure camera registries, write JSON, run scripts or understand agent internals. Use synthetic, staged or otherwise expressly authorised footage for the demo; use no live public surveillance.

## 3. Product boundaries

**Required workflow:** Officer self-registration → email verification → professional profile & supporting credential upload → District Administrator verification & approval → approved officer sign-in → new investigation → description and batch upload → Start Investigation → real backend job allocation and analysis → live progress/findings → human review → persistent history. District Administrators review applications, inspect credentials, approve/reject/request info, and suspend or reinstate officers within their assigned district without accessing officers' private investigation footage or case findings.

The initial demonstration supports ordinary visible object/vehicle categories and non-identifying attributes (e.g., red car, blue bicycle, backpack) only. Outputs are candidate visual matches, not determinations of a person's identity, location trail, guilt or criminality. No face recognition, biometric identification, individual tracking across cameras, plate-based individual identification, watchlists, live feeds, automated enforcement, or autonomous decisions affecting individuals. Do not add these later by interpreting legacy functions as required. For genuine agent demonstration use staged/synthetic object or vehicle footage and a known fixture set; no fabricated findings or timings. A human can accept a finding as relevant, not establish legal evidence or guilt.

## 4. Functional requirements

| ID | Must-have capability | Observable result |
|---|---|---|
| F01 | Supabase Auth & Officer Verification | Police officers self-register, verify email, submit professional details & supporting credentials (PDF/JPEG/PNG up to 5 MB, max 3 files), and track status. Email verification alone does not grant investigation access; a District Administrator must approve the officer first, and can later suspend or reinstate access. No shared Basic Auth or public admin registration. |
| F01b | District Administration | Server-provisioned District Administrators review district-scoped applications, inspect private verification documents, approve/reject/request info, and suspend/reinstate officers with mandatory reasons and append-only audit history, without automatic access to officers' investigation cases or footage. |
| F02 | Overview and history | User sees only their own investigations, accurate derived counts, status and recent findings. History survives refresh and re-login. |
| F03 | Create investigation | Enter title (optional), required description; declare footage authorised; server creates unique case ID. No required jurisdiction map or camera registry. |
| F04 | Batch upload | Choose several videos, review names/sizes/previews, optional recording metadata; validate, show each upload state, retry failed upload, remove before start. |
| F05 | Start analysis | Only after at least one ready video and no unresolved uploads; create durable run and one job per ready video; prevent duplicate starts. |
| F06 | Multi-agent backend | Existing LangGraph planner parses supported clue and delegates persisted tasks to a bounded pool; verify at least two workers actually claim distinct jobs in a concurrency test. |
| F07 | Realtime | Actual states and incoming candidate findings update without manual refresh; resync on reconnect; never simulate progress. |
| F08 | Candidate inspection | Show actual frame when available, source file, relative video offset, optional known recorded time, detector label and confidence; distinguish missing evidence and no matches. |
| F09 | Human review | Accept or reject relevance, optional note; persist authenticated reviewer ID and decision time, preserve prior review history. |
| F10 | Secure persistence | Supabase PostgreSQL persists cases, videos, jobs, findings and review history; private durable evidence frames remain accessible to the owner. |
| F11 | Robustness | Partial upload/job errors visible, retry safely, browser closure does not stop jobs, worker restarts recover; no duplicate candidates on retries. |
| F12 | Deployment | Single React application on Vercel; HTTPS reachable FastAPI and persistent workers on separate suitable compute; real Supabase integration verified. |

Nice to have only after F01–F12: source video playback (if retained), sorting/filtering convenience, downloadable summary of reviewed findings with source references. No map, OCR, reference-image matching, audio transcription, n8n integration, physical camera network, billing, organisation management, notification service, chat assistant, advanced analytics, multi-tenant teams, or legal evidence certification in MVP. Do not import these from older code. No public signup by default; invite-only accounts are a recommended MVP default to minimize access risks.

## 5. User stories

- As an authorised reviewer, I want secure sign-in so I can access my own work only.
- As a reviewer, I want to describe an object/vehicle and add multiple files without entering internal agent settings.
- As a reviewer, I want to know when each file is uploaded and fix an individual failure without losing successful uploads.
- As a reviewer, I want to start analysis once and see which tasks are really queued, running or complete.
- As a reviewer, I want to see candidate frames during analysis and locate the relevant video offset for verification.
- As a reviewer, I want to accept or reject candidate relevance and inspect decision history without treating AI confidence as certainty.
- As a reviewer, I want to leave and return to my investigation without losing work.
- As a second account, I must not see, edit, subscribe to or retrieve media belonging to the first account.

## 6. Non-functional requirements

Reliability: durable queue and terminal state aggregation; retry only safe operations; no silent data loss or production SQLite fallback. Security: verified access token, server-side ownership, RLS, private media, no secrets in frontend/git, authorized footage declaration, bounded upload and compute, safe logs. Usability: keyboard accessible, responsive, clear loading/empty/error states, no unnecessary screens. Performance: report actual per-video and aggregate timings, hardware and test data; do not claim a speedup without measurement. Privacy: minimize raw footage retention, no unnecessary location or personal data, no third-party upload of frames to the LLM; owner must approve retention and quotas before deploying.

## 7. Acceptance and measurable success

| ID | Pass evidence |
|---|---|
| A01 | Two invited test accounts: successful login/logout/session restoration/password reset; invalid or expired token fails safely. |
| A02 | One test user creates case and uploads at least three small staged videos, including a known non-match; failed/corrupt upload does not create a ready recording. |
| A03 | One stable start key yields one run and exactly one job per ready source. Duplicate start does not add jobs. |
| A04 | Controlled worker test shows overlapping job execution by two distinct worker identities; if single-GPU constraints force serial inference, disclose this and do not claim concurrent inference. |
| A05 | At least one genuine candidate on known-positive fixture, valid source offset, evidence frame and correct provenance; no mock findings in demo. Negative fixture and no-match path tested. |
| A06 | Realtime events update progress and findings, including reconnect recovery; actual terminal counts agree with database. |
| A07 | Accept/reject saved with verified user ID; review persists after refresh, logout/login and backend restart; no fake reviewer name. |
| A08 | Account B cannot read/write A's case, child records, evidence or subscribe to A's events; anonymous access denied. |
| A09 | Worker failure/lease expiry/retry verified without duplicate persisted findings; case ends completed, partial or failed correctly. |
| A10 | Deployed React build reaches live backend and Supabase; no sample/mock data, broken navigation, missing critical setup, or undocumented blockers. |

Original presentation target (3 simulated zones, 12 recordings, four aggregate hours, <=20 minutes first pass) was an aspirational benchmark, not a measured result or mandatory acceptance threshold for this simplified MVP. First validate a small reproducible fixture set and report actual numbers. Do not equate no findings with an error or 100% proof of absence.

## 8. Owner decisions and assumptions

Recommended defaults chosen for this specification (not confirmed provisioned): invite-only email/password, private durable evidence-frame storage, backend upload endpoint, fixed per-case one-run MVP, and dark UI. Raw video may remain in a durable private backend volume during demo; choose Supabase Storage for raw footage only after owner confirms limits, costs and retention. Owner must confirm Supabase project, backend host/GPU, domain/HTTPS, file limits, retention and test accounts. No live credentials or hardware benchmarks are supplied. Do not perform production migrations, external deployment, push or destructive cleanup without approval.
