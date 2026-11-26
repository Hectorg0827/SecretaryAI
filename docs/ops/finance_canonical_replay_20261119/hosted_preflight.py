#!/usr/bin/env python3
"""Read-only hosted preflight. No replay execution until independent review."""
import os
import subprocess
import argparse

REF = "ontqkqgofkghsdgtqwsm"
HOST = f"db.{REF}.supabase.co"

def connection_env(password, ca):
    if not password or not ca or not os.path.isfile(ca):
        raise ValueError("Branch password and trusted CA file required")
    return {"PATH": os.defpath, "PGHOST": HOST, "PGPORT": "5432",
            "PGDATABASE": "postgres", "PGUSER": "postgres",
            "PGPASSWORD": password, "PGSSLMODE": "verify-full",
            "PGSSLROOTCERT": ca, "PGCONNECT_TIMEOUT": "10",
            "PGOPTIONS": "-c default_transaction_read_only=on"}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project-ref", required=True, choices=[REF])
    args = p.parse_args()
    env = connection_env(os.environ.get("DISPOSABLE_DB_PASSWORD"), os.environ.get("DISPOSABLE_DB_CA"))
    subprocess.run(["psql", "-X", "-w", "-v", "ON_ERROR_STOP=1", "-c",
        "SELECT current_database(), current_user, (SELECT count(*) FROM pg_tables WHERE schemaname='public') AS public_tables, (SELECT count(*) FROM auth.users) AS auth_users, (SELECT count(*) FROM supabase_migrations.schema_migrations) AS versions;"],
        env=env, check=True, timeout=30)

if __name__ == "__main__":
    main()
