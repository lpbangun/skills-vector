from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import httpx

from skills_vector.api import create_app
from skills_vector.config import Settings
from skills_vector.domain import Role


class ApiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        settings = Settings(
            database_path=Path(self.tempdir.name) / "api.sqlite",
            runtime_mode="stub",
            ui_directory=None,
        )
        transport = httpx.ASGITransport(app=create_app(settings))
        self.client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.tempdir.cleanup()

    async def create_run(self, role: Role = Role.RECRUITER) -> dict[str, object]:
        response = await self.client.post(
            "/api/v1/investigations",
            json={"role": role.value, "as_of": date(2026, 8, 14).isoformat()},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_health_and_all_roles_are_exposed(self) -> None:
        health = await self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["runtime"], "stub")
        roles = await self.client.get("/api/v1/roles")
        self.assertEqual(roles.status_code, 200)
        self.assertEqual({item["role"] for item in roles.json()}, {role.value for role in Role})

    async def test_each_approved_role_can_run_and_be_retrieved(self) -> None:
        for role in Role:
            result = await self.create_run(role)
            run_id = result["run_id"]
            self.assertEqual(result["draft"]["request"]["role"], role.value)
            self.assertEqual(result["paused_before"], "human_review")
            record = await self.client.get(f"/api/v1/investigations/{run_id}")
            self.assertEqual(record.status_code, 200)
            self.assertEqual(record.json()["status"], "awaiting_human_review")
            events = await self.client.get(f"/api/v1/investigations/{run_id}/events")
            self.assertEqual(events.status_code, 200)
            self.assertGreater(len(events.json()), 10)
            brief = await self.client.get(f"/api/v1/investigations/{run_id}/brief")
            self.assertEqual(brief.status_code, 200)
            self.assertTrue(brief.json()["private"])

    async def test_review_is_durable_idempotent_and_private(self) -> None:
        run_id = (await self.create_run())["run_id"]
        payload = {
            "decision": "approved",
            "reviewer": "API reviewer",
            "note": "Evidence and uncertainty reviewed.",
        }
        first = await self.client.post(f"/api/v1/investigations/{run_id}/review", json=payload)
        second = await self.client.post(f"/api/v1/investigations/{run_id}/review", json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        record = (await self.client.get(f"/api/v1/investigations/{run_id}")).json()
        brief = (await self.client.get(f"/api/v1/investigations/{run_id}/brief")).json()
        events = (await self.client.get(f"/api/v1/investigations/{run_id}/events")).json()
        self.assertEqual(record["status"], "approved")
        self.assertEqual(brief["review"]["reviewer"], "API reviewer")
        self.assertTrue(brief["private"])
        self.assertEqual(events[-1]["node"], "human_review")
        conflict = await self.client.post(
            f"/api/v1/investigations/{run_id}/review",
            json={**payload, "decision": "rejected"},
        )
        self.assertEqual(conflict.status_code, 409)

    async def test_validation_and_missing_runs_have_clear_statuses(self) -> None:
        invalid = await self.client.post(
            "/api/v1/investigations",
            json={"role": "software_engineer", "as_of": "2026-08-14"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual((await self.client.get("/api/v1/investigations/missing")).status_code, 404)
        missing_review = await self.client.post(
            "/api/v1/investigations/missing/review",
            json={"decision": "approved", "reviewer": "Reviewer"},
        )
        self.assertEqual(missing_review.status_code, 404)


if __name__ == "__main__":
    unittest.main()
