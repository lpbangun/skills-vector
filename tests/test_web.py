from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from skills_vector.web import create_app


class WebFlowTests(unittest.TestCase):
    def test_dashboard_investigation_inspection_and_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory) / "web.db")
            with TestClient(app) as client:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn("HR Coordinator", page.text)
                self.assertIn("Recruiter", page.text)
                self.assertIn("Learning &amp; Development Specialist", page.text)
                self.assertIn("Launch investigation", page.text)

                response = client.post("/api/roles/recruiter/investigations")
                self.assertEqual(response.status_code, 201)
                run = response.json()
                self.assertEqual(run["status"], "awaiting_approval")
                self.assertEqual(run["trace"]["summary"]["stage_count"], 15)
                self.assertEqual(run["trace"]["summary"]["invocation_count"], 14)
                inspected = client.get(f"/api/runs/{run['id']}").json()
                self.assertEqual(inspected["brief"]["role_name"], "Recruiter")

                trace = client.get(f"/api/runs/{run['id']}/trace")
                self.assertEqual(trace.status_code, 200)
                self.assertEqual(len(trace.json()["edges"]), 22)
                evidence = client.get(f"/api/runs/{run['id']}/evidence", params={"cluster": "skill_shift"})
                self.assertEqual(evidence.status_code, 200)
                self.assertEqual(evidence.json()["active_cluster"], "skill_shift")
                self.assertTrue(evidence.json()["sources"])
                invocation_id = trace.json()["stages"][0]["invocations"][0]["id"]
                invocation = client.get(f"/api/runs/{run['id']}/invocations/{invocation_id}")
                self.assertEqual(invocation.status_code, 200)
                self.assertIn("raw_payload", invocation.json())

                rejected = client.post(f"/api/runs/{run['id']}/approve", json={"reviewer": "", "note": ""})
                self.assertEqual(rejected.status_code, 422)
                approved = client.post(f"/api/runs/{run['id']}/approve", json={"reviewer": "Owner", "note": "Reviewed"})
                self.assertEqual(approved.status_code, 200)
                self.assertEqual(approved.json()["status"], "approved")


if __name__ == "__main__":
    unittest.main()
