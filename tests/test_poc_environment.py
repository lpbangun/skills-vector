from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.poc_env import (
    ConfigError,
    build_run_env,
    load_config,
    run_command,
    verify_corpus,
)


class PocEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="poc-env-test-only-")
        self.root = Path(self.tempdir.name)
        self.config_path, self.config = self._write_fixture()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_fixture(self) -> tuple[Path, dict[str, object]]:
        worktree = self.root / "worktree"
        worktree.mkdir()
        corpus = self.root / "shared-corpus"
        raw = corpus / "raw"
        raw.mkdir(parents=True)
        source = raw / "example.txt"
        source.write_text("test-only corpus\n", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest = corpus / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sources": [
                        {
                            "id": "test-only-example",
                            "url": "https://example.invalid/example.txt",
                            "path": "raw/example.txt",
                            "sha256": digest,
                            "status": "present",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        lane_root = self.root / "lanes" / "lane-a"
        venv_bin = lane_root / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(sys.executable)
        config = {
            "schema_version": 1,
            "lane": "A",
            "worktree": str(worktree),
            "corpus": {
                "directory": str(corpus),
                "manifest": str(manifest),
                "read_only": True,
            },
            "lane_root": str(lane_root),
            "venv": str(lane_root / ".venv"),
            "paths": {
                "state": str(lane_root / "state" / "skills_vector.db"),
                "releases": str(lane_root / "releases"),
                "cache": str(lane_root / "cache"),
                "output": str(lane_root / "output"),
            },
            "browser": {"browsers_path": None},
            "mode": {"offline_by_default": True, "live_inference": "not_configured"},
        }
        config_path = self.root / "test-only-lane-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path, config

    def test_config_requires_absolute_path_and_unconfigured_live_mode(self) -> None:
        with self.assertRaisesRegex(ConfigError, "absolute"):
            load_config(Path("relative-config.json"))

        live_config = dict(self.config)
        live_config["mode"] = {"offline_by_default": True, "live_inference": "configured"}
        self.config_path.write_text(json.dumps(live_config), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "live inference"):
            load_config(self.config_path)

    def test_invalid_lane_path_alignment_is_rejected(self) -> None:
        invalid = dict(self.config)
        invalid["venv"] = str(self.root / "shared-venv")
        self.config_path.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "under lane_root"):
            load_config(self.config_path)

        invalid = dict(self.config)
        invalid["paths"] = dict(self.config["paths"])
        invalid["paths"]["cache"] = invalid["paths"]["releases"]
        self.config_path.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "overlap"):
            load_config(self.config_path)

    def test_corpus_hash_tampering_missing_file_and_traversal_fail_closed(self) -> None:
        config = load_config(self.config_path)
        self.assertEqual(verify_corpus(config)["present"], 1)

        source = self.root / "shared-corpus" / "raw" / "example.txt"
        source.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "hash mismatch"):
            verify_corpus(config)

        source.unlink()
        with self.assertRaisesRegex(ConfigError, "missing"):
            verify_corpus(config)

        traversal_config = dict(self.config)
        traversal_config["corpus"] = dict(self.config["corpus"])
        manifest_data = json.loads(
            (self.root / "shared-corpus" / "manifest.json").read_text(encoding="utf-8")
        )
        manifest_data["sources"][0]["path"] = "../outside.txt"
        (self.root / "shared-corpus" / "manifest.json").write_text(
            json.dumps(manifest_data), encoding="utf-8"
        )
        self.config_path.write_text(json.dumps(traversal_config), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "traversal"):
            verify_corpus(load_config(self.config_path))

    def test_retrieved_snapshot_verifies_raw_and_extracted_content(self) -> None:
        config = load_config(self.config_path)
        manifest = json.loads(config.corpus_manifest.read_text())
        manifest["schema_version"] = "skills-vector.poc-corpus.v1"
        entry = manifest["sources"][0]
        entry["status"] = "retrieved"
        extract = config.corpus_directory / "extract.txt"
        extract.write_text("test-only extract")
        entry["extract_path"] = "extract.txt"
        entry["extract_sha256"] = hashlib.sha256(extract.read_bytes()).hexdigest()
        config.corpus_manifest.write_text(json.dumps(manifest))
        result = verify_corpus(config)
        self.assertEqual(result["present"], 1)
        self.assertEqual(result["verified"], 2)
        extract.write_text("tampered")
        with self.assertRaisesRegex(ConfigError, "hash mismatch"):
            verify_corpus(config)

    def test_no_retrieved_sources_cannot_pass_preflight(self) -> None:
        config = load_config(self.config_path)
        manifest = json.loads(config.corpus_manifest.read_text())
        manifest["sources"][0]["status"] = "failed"
        config.corpus_manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ConfigError, "no retrieved"):
            verify_corpus(config)

    def test_lock_probe_checks_matching_installed_environment(self) -> None:
        from scripts.poc_env import _lock_check
        config = load_config(self.config_path)
        (config.worktree / "uv.lock").write_text("test-only stub")
        with patch("scripts.poc_env.shutil.which", return_value="/usr/bin/uv"), patch(
            "scripts.poc_env.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            self.assertTrue(_lock_check(config)["ok"])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["/usr/bin/uv", "sync", "--frozen", "--group", "dev", "--check", "--offline"], commands)
        self.assertEqual(run.call_args.kwargs["env"]["UV_PROJECT_ENVIRONMENT"], str(config.venv))

    def test_run_environment_is_offline_isolated_and_keeps_home(self) -> None:
        config = load_config(self.config_path)
        original_home = os.environ.get("HOME")
        with patch.dict(
            os.environ,
            {
                "DEEPINFRA_API_KEY": "test-only-secret",
                "CURSOR_API_KEY": "test-only-secret",
                "SKILLS_VECTOR_RUNTIME": "deepinfra",
                "VIRTUAL_ENV": "/stale/venv",
                "PATH": "/stale/venv/bin:/usr/bin",
            },
            clear=False,
        ):
            env = build_run_env(config)

        self.assertNotIn("DEEPINFRA_API_KEY", env)
        self.assertNotIn("CURSOR_API_KEY", env)
        self.assertEqual(env["SKILLS_VECTOR_RUNTIME"], "offline")
        self.assertEqual(env["SKILLS_VECTOR_MONTHLY_BUDGET_USD"], "0")
        self.assertEqual(env["SKILLS_VECTOR_DATABASE"], str(config.state))
        self.assertEqual(env["SKILLS_VECTOR_RELEASES"], str(config.releases))
        self.assertEqual(env["VIRTUAL_ENV"], str(config.venv))
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(config.venv / "bin"))
        self.assertEqual(env.get("HOME"), original_home)

    def test_run_executes_in_worktree_with_propagated_safe_environment(self) -> None:
        config = load_config(self.config_path)
        output = self.root / "command-result.json"
        with patch.dict(
            os.environ,
            {"POC_ENV_TEST_OUTPUT": str(output), "DEEPINFRA_API_KEY": "secret"},
            clear=False,
        ):
            exit_code = run_command(
                config,
                [
                    "python",
                    "-c",
                    (
                        "import json, os, pathlib; "
                        "pathlib.Path(os.environ['POC_ENV_TEST_OUTPUT']).write_text(json.dumps({" 
                        "'cwd': os.getcwd(), 'runtime': os.environ.get('SKILLS_VECTOR_RUNTIME'), "
                        "'secret': os.environ.get('DEEPINFRA_API_KEY')}))"
                    ),
                ],
            )

        self.assertEqual(exit_code, 0)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["cwd"], str(config.worktree))
        self.assertEqual(result["runtime"], "offline")
        self.assertIsNone(result["secret"])


if __name__ == "__main__":
    unittest.main()
