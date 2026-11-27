# CCTV Investigation Agent (`CCTV-AI-Agent`)

An autonomous multi-agent CCTV investigation prototype for uploading authorized video recordings, parsing natural-language object/vehicle clues, coordinating video analysis jobs, and reviewing timestamped candidate findings with human verification.

> **MVP Scope & Limitations:** Use synthetic, staged, or expressly authorized footage only. Outputs are candidate visual clues for human verification—not automated suspect identification, facial recognition, license-plate OCR, or legal proof of guilt. See `docs/source-of-truth/` for the complete MVP specification.

---

## Current Baseline vs. Target MVP

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
