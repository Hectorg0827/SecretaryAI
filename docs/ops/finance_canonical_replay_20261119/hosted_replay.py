#!/usr/bin/env python3
"""Generate SQL for reviewed disposable-only replay; never connect to a database."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

REF = "ontqkqgofkghsdgtqwsm"
BASE = Path(__file__).resolve().parent

def literal(s):
    return "'" + s.replace("'", "''") + "'"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-ref", required=True, choices=[REF])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run(["python3", str(BASE / "materialize.py"), "--target", temp], check=True)
        rows = json.loads((BASE / "manifest.json").read_text())["migrations"]
        versions = ",".join(literal(r["version"]) for r in rows[:5])
        sql = ["BEGIN;", "SET LOCAL lock_timeout='5s';",
          "LOCK TABLE supabase_migrations.schema_migrations IN ACCESS EXCLUSIVE MODE;",
          """DO $$ BEGIN
          IF current_setting('app.disposable_project_ref', true) IS DISTINCT FROM 'ontqkqgofkghsdgtqwsm' THEN RAISE EXCEPTION 'Disposable target marker missing'; END IF;
          IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public') THEN RAISE EXCEPTION 'Target not empty'; END IF;
          IF EXISTS (SELECT 1 FROM auth.users) THEN RAISE EXCEPTION 'Target has users'; END IF;
          IF (SELECT count(*) FROM supabase_migrations.schema_migrations) <> 5 OR EXISTS (SELECT 1 FROM supabase_migrations.schema_migrations WHERE version NOT IN (""" + versions + """) OR coalesce(cardinality(statements),0) <> 0) THEN RAISE EXCEPTION 'Unexpected history'; END IF;
          END $$;"""]
        for row in rows:
            source = (Path(temp)/"supabase"/"migrations"/row["filename"]).read_text()
            sql.append(source)
            sql.append("INSERT INTO supabase_migrations.schema_migrations(version,name,statements) VALUES ("+literal(row["version"])+","+literal(row["filename"][15:-4])+",ARRAY["+literal(source)+"]) ON CONFLICT(version) DO UPDATE SET statements=EXCLUDED.statements,name=EXCLUDED.name;")
        sql.append((BASE/"assert_schema.sql").read_text())
        sql.append("COMMIT;")
        with args.output.open("x") as f:
            f.write("\n".join(sql)+"\n")
if __name__ == "__main__":
    main()
