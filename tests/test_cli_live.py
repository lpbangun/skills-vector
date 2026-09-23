from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from skills_vector.catalog import CatalogStore
from skills_vector.cli import build_research_pipeline
from skills_vector.config import Settings
from skills_vector.interpret import DeepInfraInterpreter, StructuredFixtureInterpreter
from skills_vector.pipeline import ResearchPipeline, default_fixture_root


def _settings(**overrides) -> Settings:
    values = {
        "database_path": Path("data/skills_vector.db"),
        "releases_path": Path("data/releases"),
        "monthly_budget_usd": 10.0,
        "deepinfra_api_key": None,
        "runtime": "offline",
    }
    values.update(overrides)
    return Settings(**values)


class CliLiveWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = CatalogStore(Path(self.tempdir.name) / "catalog.sqlite")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_live_without_key_fails_with_actionable_error(self) -> None:
        args = Namespace(live=True, escalate_hard=False, fixtures=False)
        with self.assertRaisesRegex(ValueError, "DEEPINFRA_API_KEY"):
            build_research_pipeline(self.store, _settings(), args)

    def test_live_rejects_fixture_combination(self) -> None:
        args = Namespace(live=True, escalate_hard=False, fixtures=True)
        with self.assertRaisesRegex(ValueError, "--live cannot be combined with --fixtures"):
            build_research_pipeline(self.store, _settings(deepinfra_api_key="test"), args)

    def test_live_with_key_selects_deepinfra_interpreter(self) -> None:
        args = Namespace(live=False, escalate_hard=True, fixtures=False)
        pipeline = build_research_pipeline(self.store, _settings(deepinfra_api_key="test"), args)
        self.assertIsInstance(pipeline.interpreter, DeepInfraInterpreter)
        self.assertTrue(pipeline.interpreter.live)
        self.assertTrue(pipeline.interpreter.escalate_hard)

    def test_runtime_env_selects_live(self) -> None:
        args = Namespace(live=False, escalate_hard=False, fixtures=False)
        pipeline = build_research_pipeline(
            self.store, _settings(deepinfra_api_key="test", runtime="deepinfra"), args
        )
        self.assertIsInstance(pipeline.interpreter, DeepInfraInterpreter)

    def test_offline_default_unchanged(self) -> None:
        args = Namespace(live=False, escalate_hard=False, fixtures=True)
        pipeline = build_research_pipeline(self.store, _settings(), args)
        self.assertIsInstance(pipeline.interpreter, StructuredFixtureInterpreter)

    def test_pipeline_binds_real_run_id_to_interpreter(self) -> None:
        class BoundFixture(StructuredFixtureInterpreter):
            def __init__(self) -> None:
                self.run_id = "pending"

        interpreter = BoundFixture()
        pipeline = ResearchPipeline(self.store, default_fixture_root(), interpreter)
        run_id = pipeline.run("occ_product_manager", allow_fixtures=True)
        self.assertEqual(interpreter.run_id, run_id)


if __name__ == "__main__":
    unittest.main()
