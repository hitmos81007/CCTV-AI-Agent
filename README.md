# CCTV Investigation Agent (`CCTV-AI-Agent`)

An autonomous multi-agent CCTV investigation prototype for uploading authorized video recordings, parsing natural-language object/vehicle clues, coordinating video analysis jobs, and reviewing timestamped candidate findings with human verification.

> **MVP Scope & Limitations:** Use synthetic, staged, or expressly authorized footage only. Outputs are candidate visual clues for human verification—not automated suspect identification, facial recognition, license-plate OCR, or legal proof of guilt. See `docs/source-of-truth/` for the complete MVP specification.

---

## 🏆 Hackathon Evaluator — Quick Start Guide

Welcome hackathon judges and evaluators! This guide provides a rapid, structured path to evaluate the **CCTV Investigation Agent (`CCTV-AI-Agent`)** prototype.

### A. Application Access & Architecture Boundaries

| Endpoint / Resource | URL / Location | Purpose |
| :--- | :--- | :--- |
| **Deployed Frontend (Vercel)** | [https://cctv-ai-agent-frontend.vercel.app/](https://cctv-ai-agent-frontend.vercel.app/) | Production React SPA bundle connected to Supabase Auth & cloud storage. |
| **Local Frontend (Vite)** | `http://localhost:5173/` | Local operational dashboard connected to both Supabase and the local FastAPI backend. |
| **Local Backend API (FastAPI)** | `http://127.0.0.1:8000/` | Local REST API handling orchestration, video processing, and evidence integrity. |
| **Backend Health Endpoint** | `http://127.0.0.1:8000/health` | Instant backend status, environment validation, and subsystem health check. |
| **GitHub Repository** | [https://github.com/hitmos81007/CCTV-AI-Agent](https://github.com/hitmos81007/CCTV-AI-Agent) | Complete source code, migrations, tests, and documentation. |

> [!IMPORTANT]
> **Deployed vs. Local Execution:** The hosted Vercel frontend provides live authentication, user management, and read access against cloud Supabase. However, because local private video storage, CUDA YOLOv11 acceleration, and local Ollama (`llama3.2`) inference run on the operational workstation, **the complete end-to-end investigation pipeline** (video ingestion, multi-agent queue dispatch, realtime candidate arrival, and bounding-box evidence review) runs on the local application stack (`http://localhost:5173/` + `http://127.0.0.1:8000/`).

---

### B. Administrator Access & Police Officer Verification

To protect public privacy and satisfy law-enforcement chain-of-custody requirements, **officer registrations require explicit administrator verification** before investigation capabilities are unlocked:

1. **Owner Administrator:** Associated with `mohitsivakumar8@gmail.com` (District Administrator for `central-metro`).
2. **Evaluator Administrator:** A dedicated temporary administrator account (`evaluator.admin@cctv-agent.local`) has been pre-provisioned with minimum required district-level permissions for hackathon evaluation. Its temporary credentials are provided privately to the project owner for evaluator access.
3. **Role-Based Access Control (RBAC):** Officers with `pending_review` or `suspended` status are strictly prohibited from viewing, creating, or modifying investigation cases (HTTP 403 Forbidden).

---

### C. Two Evaluation Methods

Choose the evaluation path that matches your available time and testing goals:

#### Method 1 — Quick Demonstration (Instant 2-Minute Review)

Experience the core candidate findings gallery, evidence inspector, and human review system immediately without running a complete analysis pipeline:

1. Open `http://localhost:5173/` and sign in with an approved demonstration officer account (or evaluator credentials).
2. Navigate directly to the genuine demonstration investigation:
   - **Case Link:** `http://localhost:5173/cases/CASE-0338111273`
   - **Investigation Title:** `Genuine Demonstration — Blue Commercial Transit Sighting`
   - **Provenance & AI Execution:** Genuine end-to-end YOLOv11 execution with model-generated bounding box `[17, 230, 809, 734]`, confidence `94.3%`, source video `staged_transit_bus1.mp4` (SHA-256: `ee68d8e786...`), processed by independent CUDA worker `agent-1`. See complete provenance manifest at `docs/provenance/genuine_demonstration_provenance.json`.
3. **Inspect Realtime Candidate Findings:** View candidate cards displaying the source video (`staged_transit_bus1.mp4`), camera ID (`CAM-CENTRAL-01`), video offset timestamp (`0.0s`), detector confidence (`94.3%`), and matched clue tags (`blue`, `bus`).
4. **Inspect Evidence & Bounding Boxes:** Click **Inspect Evidence** on the candidate card:
   - **Annotated View:** View the canvas overlay with actual model-generated YOLOv11 bounding-box coordinates highlighting the detected blue transit vehicle.
   - **Unannotated View:** Toggle to the clean evidence frame. The backend validates the frame's cryptographic SHA-256 hash before serving to ensure uncorrupted chain-of-custody.
   - **Source Video Playback:** Click the source recording to seek directly to the sighting timestamp with HTTP 206 partial content streaming.
5. **Submit Human Review Decision:**
   - Click **Confirm Candidate** (records `confirmed_candidate` status with the reviewer's ID and timestamp).
   - Click **Reject Candidate** (records `rejected` status with mandatory audit notes).
   - Refresh the page, log out, or restart the backend to observe full review decision persistence in PostgreSQL.

> [!NOTE]
> **Synthetic Verification Test Fixture:** The synthetic test fixture (`CASE-DEMO-STAGED-01`) is preserved strictly as an isolated UI verification benchmark and is clearly demarcated from genuine AI pipeline results.

#### Method 2 — Complete End-to-End Investigation (5-10 Minutes)

Run the entire pipeline from officer onboarding through AI orchestration and worker detection:

1. **Register Officer:** Visit `http://localhost:5173/register` and register a test officer (e.g., `officer.evaluator@example.com`, Badge `B-4091`, District `Central Metro`).
2. **Approve Officer:** Sign in as Administrator at `http://localhost:5173/admin`, locate the pending registration, and click **Approve** for district `central-metro`.
3. **Create Investigation:** Sign in as the newly approved officer, click **New Investigation** (`/cases/new`), and enter:
   - **Title:** `Evaluation Run — Suspicious Vehicle Tracking`
   - **Description:** `Locate suspect red sedan traveling east near central intersection.`
   - **District:** `central-metro`
4. **Upload Authorized Footage:** Drag and drop authorized test clips (e.g., `staged_traffic_cam1.mp4`). The system calculates the SHA-256 hash, extracts stream metadata via `ffprobe`, and registers the recordings.
5. **Start Investigation:** Click **Start Investigation**. The system executes:
   - **LangGraph Central Orchestrator:** Sanitizes and parses natural-language clues with Ollama (`llama3.2`).
   - **Atomic PostgreSQL Job Queue:** Emits jobs with `FOR UPDATE SKIP LOCKED` leases to prevent race conditions.
   - **Independent CUDA Workers (`agent-1`, `agent-2`):** Claim jobs, run YOLOv11 + ByteTrack inference, and extract candidate frames.
   - **Supabase Realtime:** Pushes candidate sighting payloads via WebSockets to the frontend without requiring page refreshes.
6. **Review Findings:** Inspect candidate findings, verify bounding boxes, and record your human review decisions.

---

### D. Recommended Demonstration Footage & Queries

| Test Fixture | Contents | Expected Result | Recommended Query |
| :--- | :--- | :--- | :--- |
| `staged_transit_bus1.mp4` | Staged transit corridor footage with blue commercial bus. | **Genuine AI Positive Match:** Detected by YOLOv11 nano as `bus` (`conf=0.943`, bbox `[17, 230, 809, 734]`), color classified as `blue`. Realtime candidate delivery & persistent review verified. | `"Locate suspect blue bus or commercial transit vehicle operating in central corridor."` |
| `staged_traffic_negative.mp4` | Staged traffic corridor without target vehicle. | **Filtered / Negative Control:** Verified zero false-positive candidates emitted. Strict clue filtering demonstrated. | `"Locate a red sports car."` |
| `staged_traffic_cam1.mp4` | Authorized traffic clip with red sedan traversing frame. | **Positive Match:** Detected by YOLOv11 as `car` with high confidence; dominant HSV color classified as `red`. | `"Suspect vehicle is a red car or sedan moving through traffic."` |

> [!IMPORTANT]
> **Cryptographic Evidence Integrity vs. Legal Chain-of-Custody:** The SHA-256 evidence hashing implemented in this MVP cryptographically proves image and video file integrity (detecting byte corruption, tampering, or unintended format transcoding). It does NOT constitute a full statutory or forensic chain of custody under rules of evidence, which requires legal custody logging, physical evidence seal protocols, and sworn officer affirmations.

> [!NOTE]
> **Detector Limitations:** Detection accuracy depends on object visibility, resolution (minimum 32x32 pixels), and lighting. Extreme occlusions or unilluminated night scenes may reduce confidence scores.

---

### E. System Architecture & Multi-Agent Flow

```mermaid
flowchart TD
    subgraph Client["Frontend Layer (React 19 + TypeScript + Vite)"]
        UI["Command Center UI\n(Dashboard, Workspace, Review Gallery)"]
        RT["Supabase Realtime\nWebSocket Client"]
        Inspector["Evidence Inspector\n(SHA-256 Verified Bounding Box Overlay)"]
    end

    subgraph Auth["Security & Identity"]
        SupaAuth["Supabase Auth\n(JWT Bearer Tokens)"]
        AdminGate["District Admin Verification\n(Approved Officer RBAC)"]
    end

    subgraph Backend["Core Backend (FastAPI + Uvicorn)"]
        API["FastAPI REST API\n(/api/v1/cases, /videos, /sightings)"]
        HashVerify["Evidence Integrity Engine\n(SHA-256 Frame & Video Hash Verification)"]
    end

    subgraph Orchestrator["AI Orchestration Layer"]
        LangGraph["LangGraph Workflow Engine\n(StateGraph + Conditional Routing)"]
        Ollama["Ollama LLM (llama3.2)\n(Clue Parsing & Schema Sanitization)"]
    end

    subgraph Queue["Durable Job Leases (PostgreSQL)"]
        DB[("Supabase PostgreSQL\n(Migrations 001–007, RLS, Audit Trail)")]
        Jobs["investigation_jobs\n(FOR UPDATE SKIP LOCKED)"]
    end

    subgraph Workers["CUDA Processing Agents (RTX 5050 / CPU)"]
        W1["Worker: agent-1\n(YOLOv11n + ByteTrack)"]
        W2["Worker: agent-2\n(YOLOv11n + ByteTrack)"]
    end

    UI -->|JWT Bearer| API
    API -->|JWKS Validate| SupaAuth
    API --> AdminGate
    API --> LangGraph
    LangGraph -->|Extract Clues| Ollama
    LangGraph -->|Enqueue Jobs| Jobs
    Jobs -->|Lease Claim| W1
    Jobs -->|Lease Claim| W2
    W1 -->|Save Sightings & Frames| DB
    W2 -->|Save Sightings & Frames| DB
    DB -->|PostgreSQL CDC / Broadcast| RT
    RT -->|Live Sighting Push| UI
    UI --> Inspector
    Inspector -->|GET /sightings/{id}/frame| HashVerify
    HashVerify -->|Verify SHA-256| DB
```

---

### F. Technology Stack & Versions

- **Frontend:** React `19.0.0`, TypeScript `5.6`, Vite `6.0`, TailwindCSS `3.4`, Lucide React, `@supabase/supabase-js` `2.48`.
- **Backend API:** FastAPI `0.115`, Python `3.12.7`, Uvicorn `0.32`, Psycopg `3.2` (PostgreSQL client with binary connection pools).
- **Computer Vision & Inference:** PyTorch `2.7.0` / `2.11.0` (CUDA `12.8` support for NVIDIA Blackwell `sm_120`), Ultralytics YOLOv11 (`yolo11n.pt`), OpenCV `5.1.0`, ByteTrack tracker.
- **AI Orchestration:** LangGraph `0.2.60`, Ollama (`llama3.2:latest`).
- **Database & Storage:** Supabase PostgreSQL with migrations `001`–`007`, Row-Level Security (RLS), Supabase Storage for private evidence buckets, Supabase Realtime Channels.

---

### G. Local Setup & Execution Guide

#### 1. Prerequisites

- **OS:** Windows 10/11 (PowerShell recommended)
- **Python:** Python 3.12 (`uv` package manager recommended)
- **Node.js:** Node.js >= 20, npm >= 10
- **Ollama:** Running locally with `llama3.2:latest` model pulled:
  ```powershell
  ollama serve
  ollama pull llama3.2:latest
  ```

#### 2. Starting Required Local Daemons

Open separate PowerShell terminals for each background service:

```powershell
# Terminal 1: FastAPI Backend
cd CCTV-AI-Agent
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: React Frontend Development Server
cd CCTV-AI-Agent\frontend\react
npm run dev

# Terminal 3: CUDA Processing Worker 1
cd CCTV-AI-Agent
.\.venv\Scripts\python.exe -m app.worker --worker-id agent-1

# Terminal 4: CUDA Processing Worker 2
cd CCTV-AI-Agent
.\.venv\Scripts\python.exe -m app.worker --worker-id agent-2
```

> [!TIP]
> **CPU Fallback:** If testing on a machine without an NVIDIA GPU, set `$env:YOLO_DEVICE="cpu"` before starting the workers. YOLOv11n runs smoothly on modern multi-core CPUs.

---

### H. Evaluator Troubleshooting Matrix

| Symptom | Probable Cause | Immediate Remediation |
| :--- | :--- | :--- |
| `403 Forbidden` on case creation | Logged-in user is not verified or district is unapproved. | Sign in as District Admin at `/admin` and approve the officer profile for `central-metro`. |
| `409 Conflict: Clue parsing unavailable` | Case is already in `running` or `completed` state. | Clue parsing is strictly immutable once an investigation starts. Create a new case draft to edit clues. |
| Findings not appearing in realtime | Realtime channel disconnected or behind corporate firewall. | Check browser console WebSocket status; ensure local worker is actively running and processing claimed jobs. |
| Video playback error | Missing partial content support or wrong codec. | Backend supports HTTP 206 Byte-Range streaming. Ensure test clips are standard H.264/MP4 files. |
| Evidence frame hash mismatch error | Stored frame corrupted or altered on disk. | The backend rejects mismatched evidence frames to preserve chain of custody. Ensure frames in `data/evidence/frames/` remain unmodified. |

---

### I. Security, Integrity & Privacy Safeguards

1. **Human-in-the-Loop Mandate:** Automated detections are strictly classified as **Candidate Findings**. No automated warrant, arrest, or suspect action is ever triggered by the AI. Every finding requires an explicit officer review decision.
2. **Cryptographic Chain of Custody:**
   - Original video files are hashed with **SHA-256** upon ingestion.
   - Captured evidence frames are stored unannotated alongside their authoritative SHA-256 digest.
   - Bounding-box annotations are rendered dynamically on canvas overlays or via the dedicated annotated endpoint; the original evidence frame bytes are never overwritten.
3. **Database Row-Level Security (RLS):** Cross-owner data isolation ensures officers can only view or review cases within their assigned jurisdictional boundaries.
4. **Investigation Immutability:** Once an investigation begins, the video list, clue parameters, and input snapshots are cryptographically locked.

---

### J. Prototype Scope & Boundaries

To ensure transparent evaluation, please note the designed boundaries of this MVP prototype:

- ✅ **Supported:** Multi-video CCTV ingestion, natural-language vehicle/object clue parsing, parallel multi-worker inference (person, car, motorcycle, bus, truck), dominant color classification, bounding-box evidence review, persistent audit logging.
- ❌ **Explicitly Excluded & Prohibited:** Facial recognition, biometric person identification, automated license plate reading (ALPR/ANPR), cross-camera persistent identity re-identification, automated suspect matching.

---

## Current Baseline (Phase 6 Final Closure)

This repository is undergoing phased consolidation toward the target MVP defined in `docs/source-of-truth/06_Implementation_Plan.md`:

- **Current Phase 4 Baseline (Local Verified; Awaiting Owner Verification):**
  - Official frontend consolidated to **React 19 + TypeScript + Vite** (`frontend/react/`) with dark operational command-center UI, Supabase Auth (`@supabase/supabase-js`), officer verification onboarding, District Administrator review dashboard, and interactive multi-video **New Investigation** workspace (`Create.tsx` / `Workspace.tsx`).
  - Isolated **Python 3.12** environment managed with `uv` (`requirements.txt`, `requirements-video.txt`) plus **FFmpeg 9.1 / `ffprobe`** (`FFPROBE_PATH`) and **OpenCV 5.1.0**.
  - **FastAPI** backend (`app/main.py`, `app/auth.py`, `app/verification_routes.py`, `app/ingestion.py`) with Supabase JWKS Bearer JWT verification, authoritative database approval/suspension checks, ordered **PostgreSQL** migrations (`migrations/postgresql/001`–`006`), streaming multi-video ingestion (`POST /api/v1/videos`, HTTP Range playback `GET /api/v1/videos/{id}/file`, draft deletion `DELETE /api/v1/videos/{id}`), and disabled legacy prototype routes (`410 Gone`).
  - **Upcoming Phases (Not Yet Implemented):**
    - **Phase 5:** Multi-worker concurrency (`FOR UPDATE SKIP LOCKED`), parallel AI orchestration, and RTX 5050 Blackwell (`sm_120`) CUDA acceleration.
    - **Phase 6–9:** Realtime findings integration, human review history UI polish, end-to-end testing, and production deployment.

---

## Development Prerequisites

- **OS:** Windows 10/11 (PowerShell)
- **Python:** Python 3.12 (isolated `.venv` via `uv`)
- **Node.js & npm:** Node.js >= 20, npm >= 10
- **Ollama:** Installed and running locally on `http://127.0.0.1:11434` with `llama3.2:latest` pulled:
  ```powershell
  ollama pull llama3.2:latest
  ollama list
  ```
- **Deferred Prerequisites:**
  - **FFmpeg & `ffprobe`:** Required before Phase 4 video ingestion verification.
  - **CUDA 12.8 (`cu128`) PyTorch:** Required in Phase 5 for NVIDIA RTX 5050 Mobile (Blackwell `sm_120` architecture; requires PyTorch >= 2.7.0 built with `cu128` wheels and NVIDIA driver supporting CUDA 12.8+). Currently running CPU-only PyTorch.

---

## Environment Setup

### 1. Backend (Python 3.12 + `uv`)

```powershell
uv venv .venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements-video.txt
```

Copy `.env.example` to `.env` for local development (never commit `.env` or secrets):

```powershell
Copy-Item .env.example .env
```

### 1b. Database Migrations & District Admin Provisioning (Supabase PostgreSQL)

When `DATABASE_URL` is configured in `.env`, inspect and apply ordered PostgreSQL migrations (`migrations/postgresql/001`–`005`) using:

```powershell
.\.venv\Scripts\python.exe -m app.migrations --status
.\.venv\Scripts\python.exe -m app.migrations --dry-run
.\.venv\Scripts\python.exe -m app.migrations --apply
```

To provision or revoke a District Administrator after the user has registered and verified their email in Supabase Auth:

```powershell
.\.venv\Scripts\python.exe scripts/provision_admin.py --email admin@example.com --district north-division
.\.venv\Scripts\python.exe scripts/provision_admin.py --email admin@example.com --district north-division --revoke
```

### 2. Frontend (React + Vite)

Copy `frontend/react/.env.example` to `frontend/react/.env.local` and set `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`, then install dependencies:

```powershell
Copy-Item frontend/react/.env.example frontend/react/.env.local
cd frontend/react
npm ci
```

---

## Running Locally

### Start FastAPI Backend (Local SQLite Dev Mode)

From the repository root with `.venv` active:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify liveness at `http://127.0.0.1:8000/health`.

### Start React Development Server

From `frontend/react/` (or `frontend/`):

```powershell
npm --prefix frontend run dev
```

Vite proxies `/api` and `/health` to `http://127.0.0.1:8000` (configured in `frontend/react/vite.config.ts`).

### Production Frontend Build

From `frontend/` (reproducible clean build):

```powershell
npm run build
```

This runs `npm --prefix react ci && npm --prefix react run build`, outputting the production bundle to `frontend/react/dist/`.

---

## Vercel Deployment Configuration

- **Recommended Vercel Root:** `frontend/react` (with `npm ci`, `npm run build`, output `dist`, and `frontend/react/vercel.json` SPA rewrites).
- **Compatible Legacy Root (`frontend/`):** `frontend/vercel.json` is configured with `"buildCommand": "npm run build"`, `"outputDirectory": "react/dist"`, and SPA fallback rewrites to `/index.html`, ensuring clean environments install `frontend/react` dependencies before building.
- Set `VITE_API_BASE_URL` in Vercel to the HTTPS backend origin once backend authentication and hosting are ready. **Do not expose an unauthenticated backend publicly before Phase 3 Supabase Auth is implemented.**

---

## Running Tests

### Backend Contract Suite

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

---

## Phase 5: Central AI Orchestrator, Durable Workers & GPU Inference

### GPU vs CPU Dependency Setup

- **CPU fallback:** `uv pip install --python .\.venv\Scripts\python.exe -r requirements-video.txt`
- **NVIDIA RTX 5050 (Blackwell, CUDA 12.8):** Install PyTorch & TorchVision `cu128` wheels (do not use `cu124` on Blackwell):
  ```powershell
  uv pip install --python .\.venv\Scripts\python.exe --reinstall --index-url https://download.pytorch.org/whl/cu128 torch torchvision
  ```

### Running the Phase 5 Stack Locally

1. **Ollama (local clue parser):**
   ```powershell
   ollama serve
   ollama pull llama3.2:latest
   ```
2. **PostgreSQL Migrations (Supabase):**
   ```powershell
   .\.venv\Scripts\python.exe -m app.migrations --dry-run
   .\.venv\Scripts\python.exe -m app.migrations --apply
   ```
3. **FastAPI Backend:**
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
4. **Durable Analysis Worker(s):**
   ```powershell
   # Single process with bounded concurrency (1-4 threads)
   .\.venv\Scripts\python.exe -m app.worker --concurrency 2
   # Or run multiple independent worker processes:
   .\.venv\Scripts\python.exe -m app.worker --worker-id agent-1
   .\.venv\Scripts\python.exe -m app.worker --worker-id agent-2
   ```
5. **Frontend:**
   ```powershell
   npm --prefix frontend/react run dev
   ```

### Benchmark & Limitations

- Benchmarked on `NVIDIA GeForce RTX 5050 Laptop GPU` (8,151 MiB VRAM, Driver `573.13`, CUDA `12.8`) with `torch==2.11.0+cu128` and `yolo11n.pt`.
- Single-worker GPU inference on 3x3s clips took `8.638s` (`0.388s` model init, `105.53 MiB` peak VRAM). Two concurrent GPU worker processes achieved `10.23s` overlapping execution (`92.83 MiB` peak VRAM each) alongside `llama3.2:latest` (~2.5 GiB VRAM).
- **Known limitations:** Live Supabase Realtime finding streams and human review UI remain Phase 6; local backend (`127.0.0.1:8000`) is not publicly exposed for the hosted Vercel frontend.

---

## Legacy & Deprecated Components

The following components are retained in the repository for reference only and are **not part of the MVP workflow**:

- `app/static/review.html` and `frontend/build-legacy.mjs` (`npm --prefix frontend run build:legacy`) — Legacy single-file HTML interface.
- `n8n/` — Legacy n8n workflow exports.
- `app/onvif_nvr.py` & `requirements-onvif.txt` — Legacy ONVIF/NVR camera replay integration.
- `requirements-audio.txt` & `/api/v1/clues/transcribe` — Legacy Whisper audio transcription.
- Direct zone-bypass endpoints (`/api/v1/zones/analyze`, `/api/v1/zone/analyze`) and simulated cross-zone follow-ups.
