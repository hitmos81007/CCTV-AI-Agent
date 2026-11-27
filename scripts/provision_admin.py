"""Controlled server-side operator script to provision or revoke a District Administrator."""
from __future__ import annotations

import argparse
import os
import uuid
from datetime import datetime, timezone

import psycopg
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Provision or revoke a District Administrator in public.district_admins."
    )
    parser.add_argument("--email", required=True, help="Verified Supabase Auth user email")
    parser.add_argument("--district", required=True, help="District ID (e.g. central-metro)")
    parser.add_argument("--revoke", action="store_true", help="Deactivate administrator access")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL must be configured in .env")

    email = args.email.strip().lower()
    district_id = args.district.strip()
    now = datetime.now(timezone.utc).isoformat()

    with psycopg.connect(database_url, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, email_confirmed_at FROM auth.users WHERE lower(email) = %s", (email,))
            user_row = cur.fetchone()
            if not user_row:
                raise SystemExit(
                    f"No auth.users account found for {email}. "
                    "Create and confirm the user in Supabase Auth first."
                )
            user_id = str(uuid.UUID(str(user_row[0])))
            if not user_row[1]:
                raise SystemExit(f"User {email} has not confirmed their email address yet.")

            cur.execute("SELECT id, is_active FROM public.districts WHERE id = %s", (district_id,))
            dist_row = cur.fetchone()
            if not dist_row or not dist_row[1]:
                raise SystemExit(f"District '{district_id}' does not exist or is inactive.")

            if args.revoke:
                cur.execute(
                    "UPDATE public.district_admins SET is_active = false, updated_at = %s WHERE user_id = %s",
                    (now, user_id),
                )
                conn.commit()
                print(f"REVOKED district admin access for user_id={user_id} ({email})")
                return 0

            cur.execute(
                """
                INSERT INTO public.district_admins(user_id, district_id, is_active, provisioned_at, updated_at)
                VALUES (%s, %s, true, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET district_id = EXCLUDED.district_id,
                    is_active = true,
                    updated_at = EXCLUDED.updated_at
                """,
                (user_id, district_id, now, now),
            )
            conn.commit()
            print(f"PROVISIONED district admin: user_id={user_id} ({email}) -> district={district_id}")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
