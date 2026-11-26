import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import hosted_preflight as preflight

BASE = Path(__file__).resolve().parent
class HostedReplayTests(unittest.TestCase):
    def test_reject_parent(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/"replay.sql"
            result = subprocess.run([sys.executable,str(BASE/"hosted_replay.py"),"--project-ref","xerktvdgjvjozvckjyfp","--output",str(out)],capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(out.exists())
    def test_canonical_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/"replay.sql"
            subprocess.run([sys.executable,str(BASE/"hosted_replay.py"),"--project-ref",preflight.REF,"--output",str(out)],check=True)
            sql=out.read_text()
            rows=json.loads((BASE/"manifest.json").read_text())["migrations"]
            positions=[sql.index("VALUES ('"+r["version"]+"',") for r in rows]
            self.assertEqual(len(positions),46)
            self.assertEqual(positions,sorted(positions))
            self.assertTrue(sql.startswith("BEGIN;"))
            self.assertTrue(sql.endswith("COMMIT;\n"))
    def test_endpoint_is_pinned(self):
        with tempfile.NamedTemporaryFile() as ca, patch.dict(os.environ,{"PGHOST":"wrong","PGSERVICE":"wrong","PGHOSTADDR":"127.0.0.1"}):
            env=preflight.connection_env("fake",ca.name)
            self.assertEqual(env["PGHOST"],preflight.HOST)
            self.assertEqual(env["PGSSLMODE"],"verify-full")
            self.assertNotIn("PGHOSTADDR",env)
            self.assertNotIn("PGSERVICE",env)
    def test_missing_secret(self):
        with self.assertRaises(ValueError):
            preflight.connection_env(None,None)
if __name__ == "__main__":
    unittest.main()
