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
                inspected = client.get(f"/api/runs/{run['id']}").json()
                self.assertEqual(inspected["brief"]["role_name"], "Recruiter")

                rejected = client.post(f"/api/runs/{run['id']}/approve", json={"reviewer": "", "note": ""})
                self.assertEqual(rejected.status_code, 422)
                approved = client.post(f"/api/runs/{run['id']}/approve", json={"reviewer": "Owner", "note": "Reviewed"})
                self.assertEqual(approved.status_code, 200)
                self.assertEqual(approved.json()["status"], "approved")


if __name__ == "__main__":
    unittest.main()
