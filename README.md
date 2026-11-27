# CCTV Investigation Agent (`CCTV-AI-Agent`)

An autonomous multi-agent CCTV investigation prototype for uploading authorized video recordings, parsing natural-language object/vehicle clues, coordinating video analysis jobs, and reviewing timestamped candidate findings with human verification.

> **MVP Scope & Limitations:** Use synthetic, staged, or expressly authorized footage only. Outputs are candidate visual clues for human verification—not automated suspect identification, facial recognition, license-plate OCR, or legal proof of guilt. See `docs/source-of-truth/` for the complete MVP specification.

---

## Current Baseline vs. Target MVP

This repository is undergoing phased consolidation toward the target MVP defined in `docs/source-of-truth/06_Implementation_Plan.md`:

- **Current Phase 3 Baseline (Local Verified; Awaiting Owner Supabase Live Verification):**
  - Official frontend consolidated to **React 19 + TypeScript + Vite** (`frontend/react/`) with dark operational command-center UI, Supabase Auth (`@supabase/supabase-js`), officer verification onboarding, and District Administrator review dashboard.
  - Isolated **Python 3.12** environment managed with `uv` (`requirements.txt`, `requirements-video.txt`).
  - **FastAPI** backend (`app/main.py`, `app/auth.py`, `app/verification_routes.py`) with Supabase JWKS Bearer JWT verification, authoritative database approval/suspension checks, ordered **PostgreSQL** migrations (`app/migrations.py`, `migrations/postgresql/001`–`005`), district-scoped admin review, `case_events` + `verification_events` Realtime tables, private `cctv-evidence` and `officer-verification` Supabase Storage (`app/storage.py`), and disabled legacy prototype routes (`410 Gone`).
  - **District Admin Provisioning:** Server-side CLI `scripts/provision_admin.py` (never via public signup).
  - **Ollama** (`llama3.2:latest`) structured clue parsing (`app/ollama_client.py`).
  - **YOLO11n + ByteTrack** (`app/video_analysis.py`, `yolo11n.pt`) running in **CPU inference mode** (`torch` CPU wheel).
- **Upcoming Phases (Not Yet Implemented):**
  - **Phase 4:** Simplified case creation and batch multi-video upload UI (requires `ffmpeg`/`ffprobe`).
  - **Phase 5:** Multi-worker concurrency (`FOR UPDATE SKIP LOCKED`) and RTX 5050 Blackwell (`sm_120`) CUDA acceleration.
  - **Phase 6–9:** Findings integration, human review history UI polish, end-to-end testing, and production deployment.

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

## Legacy & Deprecated Components

The following components are retained in the repository for reference only and are **not part of the MVP workflow**:

- `app/static/review.html` and `frontend/build-legacy.mjs` (`npm --prefix frontend run build:legacy`) — Legacy single-file HTML interface.
- `n8n/` — Legacy n8n workflow exports.
- `app/onvif_nvr.py` & `requirements-onvif.txt` — Legacy ONVIF/NVR camera replay integration.
- `requirements-audio.txt` & `/api/v1/clues/transcribe` — Legacy Whisper audio transcription.
- Direct zone-bypass endpoints (`/api/v1/zones/analyze`, `/api/v1/zone/analyze`) and simulated cross-zone follow-ups.
