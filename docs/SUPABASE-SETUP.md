# PostgreSQL and Supabase setup

The application uses the existing SQLite database when `DATABASE_URL` is empty. Set `DATABASE_URL` to use a PostgreSQL database for application records. This selects a separate database. Existing SQLite records and evidence are not copied automatically.

1. Create a dedicated Supabase project and database. In the project dashboard, open **Connect** and copy its PostgreSQL connection string. On an IPv4-only network, use the **Session pooler** connection. Include `sslmode=require`, or configure `sslmode=verify-full` and the server certificate for identity verification.
2. Install the Python requirements, including `psycopg[binary]`, in the same environment used to launch FastAPI. The existing `setup_gpu.ps1` installs `requirements.txt` into the GPU package directory.
3. Set `DATABASE_URL` in the local ignored `.env` file. Keep the database password there, outside Git, and do not put it in the browser frontend. Percent-encode special characters in a URL password.
4. The application applies the numbered SQL files in `migrations/postgresql/` on startup using its database connection. Alternatively, run them in numerical order in the Supabase SQL Editor before starting the backend. They create the case, video, sighting, follow-up, audit, camera, recording, job, investigation state, orchestration and metadata tables. They enable row-level security and create no Data API policies. The database user running the backend must own the tables or have equivalent permissions.
5. Restart FastAPI and check its startup output. Create a fictional investigation and verify it remains available after a second restart. Configure `CORS_ALLOWED_ORIGINS` as an exact comma-separated list of frontend origins and set reviewer credentials in the local `.env`.

Set `CCTV_IMPORT_ROOTS` to an OS path-separated list of permitted local folders before importing DVR files. Set `CCTV_ALLOWED_RTSP_HOSTS` to a comma-separated list of permitted RTSP hosts before capturing. Stream credentials for a camera such as `CAM-1` are read from `CCTV_RTSP_USER_CAM_1` and `CCTV_RTSP_PASSWORD_CAM_1`. Keep these values in the ignored local environment file. Set `FFPROBE_PATH` if FFprobe is not on PATH. Without it, ingestion uses OpenCV metadata instead.

Start a processing worker in the backend environment with `python -m app.worker`. It claims queued LangGraph jobs, records a lease and retries interrupted jobs. The default lease is 900 seconds. The worker must remain running for queued jobs to execute. Source videos and derived images stay on the backend filesystem.

To migrate an existing SQLite backup, set `DATABASE_URL` in the process environment and run `python scripts/migrate_sqlite_to_postgres.py --sqlite PATH_TO_BACKUP` for a rolled-back comparison. Add `--apply` to commit. The tool retains original SQLite audit events in `imported_sqlite_audit` and preserves the active PostgreSQL audit chain.

The ONVIF adapter requires the optional `requirements-onvif.txt` dependencies and compatible authorized NVR hardware. Device recording discovery and replay behavior have not been validated on a physical NVR.

Supabase documentation covers [database connection options](https://supabase.com/docs/guides/database/connecting-to-postgres) and [Data API security](https://supabase.com/docs/guides/api/securing-your-api).
