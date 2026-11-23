# CCTV Investigation Agent — local demo

This is a local, human-reviewed visual-clue search workflow. It accepts a plain-language clue and searches registered clips in four directions: North, West, South, and East. Supported detector classes are people, bicycles, cars, motorcycles, buses, and trucks. Person matching is limited to broad visible type/color cues; marks, tattoos, scars, bags, and similar details are reviewer notes and are not machine-verified. The system performs no face recognition, reference-photo matching, license-plate OCR, or identity claims. A reviewer must confirm a person candidate before neighboring-zone searches. Use synthetic or public footage only.

## Current state (2026-11-23)

| Component | Verified state |
|---|---|
| Backend contract suite | 27 tests pass in the local video environment. Coverage includes four-zone guarding and batch dispatch, person review-gated follow-ups, reviewer authentication, clue parsing contracts, SHA-256/audit integrity, upload/review routes, and scheduled n8n follow-up export wiring. Local Whisper is mocked in tests. |
| Four-zone batch API | `POST /api/v1/zones/analyze` accepts North/West/South/East work items, validates the case map, and leaves missing clips in `awaiting_local_video` without creating sightings. |
| Actual detector path | PyTorch 2.14.1+cpu, OpenCV 5.1.0, and Ultralytics 8.4.216 are installed in `C:\Users\mohit\Documents\Codex\cctv-video-env`. A five-frame synthetic blank MP4 was read and passed through YOLO11n + ByteTrack; it returned zero candidate tracks, as expected. No detections were invented. |
| Actual sample MP4 runs | Both user-supplied MP4s decode and were processed locally. The 24,961,070-byte clip produced 151 raw candidate tracks on CPU (107.1 seconds); the 90,549,639-byte clip produced 162 on CPU (172.7 seconds). These are unreviewed detector outputs, not confirmed sightings, and were not saved into the case database because camera, jurisdiction, recording time, and footage provenance were not established. |
| RTX 5050 GPU | A pinned CUDA PyTorch 2.11.0+cu128 / torchvision 0.26.0+cu128 runtime was installed in `C:\Users\mohit\Documents\Codex\cctv-video-gpu-packages`; CUDA reports available on the NVIDIA GeForce RTX 5050 Laptop GPU. The 24,961,070-byte sample was processed with CUDA in 48.8 seconds and yielded 151 raw, unreviewed candidate tracks (cars 113, people 26, trucks 6, buses 6). Results were not persisted. This validates a local inference path, not a completed four-feed case. |
| Ollama and live n8n run | On 2026-11-23, the new unpublished four-direction draft completed a manual n8n → FastAPI → local Ollama run (HTTP 201). Ollama extracted a synthetic person clue with navy color, red backpack, 10:15, and North. Four work items reached the no-video guard. The isolated database has one case, zero videos, zero sightings, and zero follow-ups; integrity returned valid. |
| Live n8n workflow | The original saved draft remains untouched. A separate unpublished draft, `CCTV Investigation Orchestrator — Four Directions` (workflow ID `ZmpeTl350Tou60AR`), is loaded in the editor with 15 nodes and the `/api/v1/zones/analyze` batch endpoint. Its manual synthetic run completed through Ollama and the four-item no-video guard. The actual video-analysis node did not run because no clips were registered. |
| Follow-up workflow expression | Both importable n8n JSON files now use a valid dynamic URL expression and include a regression test for the scheduled list → split → analyze chain. The live n8n instance could not be reached during the latest check, so that fix is not yet applied to the saved live draft. |
| Local review page | A police-facing four-step flow is implemented: create case and clue, upload clips by direction with camera/time metadata, run the search, then review timestamped candidates. It calls the FastAPI case, upload, resolve, batch-analysis, and review APIs. Voice capture/transcription controls and reviewer actions are present. Basic authentication requires `REVIEW_USERNAME` and `REVIEW_PASSWORD`. Contract tests cover the page and routes; a browser-authenticated full case has not yet been completed. |
| Voice clue | The endpoint uses local `faster-whisper` when installed and deletes its temporary audio file after transcription. Actual transcription is untested. Installation currently fails under Python 3.15 because the `onnxruntime` wheel dependency is unavailable for that interpreter. |
| Follow-up search | The API queues neighboring-zone work after a person candidate is human-confirmed. The scheduled n8n follow-up POST is not yet tested against a real ready job. |
| Integrity | SHA-256 source/evidence hashing and a hash-chained audit log are implemented. Prior isolated synthetic-case checks returned valid integrity. |

The revised live n8n workflow is an unpublished draft; the previous workflow remains preserved. The blank synthetic video was processed separately and no findings from it were added to the case database.

## Windows setup

Run FastAPI on Windows so it can reach Ollama at `127.0.0.1:11434` and store evidence locally. The n8n Docker container calls the host through `host.docker.internal`.

1. Install/run Ollama and confirm `llama3.2:latest` appears in `ollama list`.
2. Copy `.env.example` to `.env`; set unique `REVIEW_USERNAME` and `REVIEW_PASSWORD` values before opening the review page.
3. In PowerShell, enter this project folder and run `./run_backend_video.ps1`. The video packages are installed in the short-path environment `C:\Users\mohit\Documents\Codex\cctv-video-env`. To force CPU, run `./run_backend_video.ps1 -Device cpu`.
4. Check `http://localhost:8000/health`, then open `http://localhost:8000/` for the protected review/upload page.
5. To enable RTX 5050 inference with the tested CUDA packages, run `./setup_gpu.ps1` once, then `./run_backend_gpu.ps1`. The script uses the local Python 3.14 runtime and a separate package directory at `C:\Users\mohit\Documents\Codex\cctv-video-gpu-packages`; confirm `/health` reports GPU readiness after launch. The CUDA inference path was tested against one uploaded sample clip.
6. Local voice transcription needs `faster-whisper` and a compatible `onnxruntime`. Python 3.15 currently lacks the required ONNX Runtime wheel; install it in a supported Python environment before enabling that optional feature. The voice transcript can alternatively be typed into the clue description.

Keep port 8000 on the host/Docker network; do not expose it publicly. Evidence is stored in `data/evidence`; SQLite is stored under `data/` by default.

## Search flow

1. Enter or dictate a clue, then review the transcribed words. Ollama extracts explicit target type, color, visible attribute notes, approximate time, and starting direction.
2. The planner dispatches four directional workers. Each receives only clips registered for that direction. Missing clips stay waiting and never reach YOLO.
3. Upload synthetic/public clips in the protected review page or through `POST /api/v1/videos`, supplying `case_id`, `jurisdiction`, `camera_id`, actual `recorded_at` with timezone, and the video file.
4. FastAPI tracks person, bicycle, car, motorcycle, bus, and truck classes inside each clip. Per-clip track IDs do not identify the same person across cameras. The person color estimate uses the torso crop and avoids the head.
5. Candidate sightings retain timestamps, offsets, camera, class, approximate color, confidence, and evidence hash. Appearance details such as scars, tattoos, clothing marks, and plate absence are not machine-verified.
6. A human checks each clip. Only a reviewer-confirmed person candidate may start neighboring-zone searches. Vehicle/object follow-ups use configured type/color ranking. Follow-ups remain waiting until a matching local clip exists.
7. Review labels are `confirmed_candidate`, `not_relevant`, or `needs_more_review`; they are not determinations of guilt or enforcement outcomes.

## API and tests

The n8n export calls `POST /api/v1/zones/analyze` for four-direction batches. Single-clip analysis remains available at `POST /api/v1/zone/analyze`. Evidence integrity can be checked with `GET /api/v1/integrity`.

Run the backend contract suite from this folder:

```powershell
& 'C:\Users\mohit\Documents\Codex\cctv-video-env\Scripts\python.exe' -m unittest discover -s tests -v
```

The suite currently passes 26/26. The synthetic empty-clip smoke test passed and returned zero tracks. The revised live n8n workflow completed a manual synthetic run: Ollama parsed the clue, four zone work items reached the no-video guard, and the isolated database integrity check passed with zero sightings. CUDA inference is verified on one real sample file, but no case data was created from it. A complete four-direction case with registered camera times, actual audio transcription, and a reviewer-confirmed follow-up cycle still needs testing.

