from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from adaptive_skills.catalog import Catalog
from adaptive_skills.config import Settings
from adaptive_skills.database import Database
from adaptive_skills.errors import ConflictError, ValidationError
from adaptive_skills.evaluation import (
    DIMENSION_WEIGHTS,
    EvaluationService,
    OpenAICompatibleRunner,
)
from adaptive_skills.llm_config import LLMConfig, LLMConfigStore, LLMProfile
from adaptive_skills.scanner import CatalogScanner
from adaptive_skills.sources import SourceManager
from adaptive_skills.taxonomy import CORE_L1, Taxonomy
from tests.helpers import commit_all, init_repo, write_skill


def valid_output(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category_l1": "演示与文档",
        "category_l2": "技术文档",
        "category_candidate": True,
        "problem": "技术文档结构不稳定",
        "use_case": "编写架构说明和操作指南",
        "notes": "方法完整，依赖较少。",
        "tags": ["文档", "架构"],
        "confidence": 0.82,
        "dimensions": {name: 8 for name in DIMENSION_WEIGHTS},
    }
    payload.update(overrides)
    return payload


class FakeRunner:
    def __init__(self, output: dict[str, Any]):
        self.output = output
        self.calls: list[tuple[LLMConfig, str, dict[str, Any]]] = []

    def run(
        self, config: LLMConfig, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((config, prompt, schema))
        return self.output


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, profile_id: str) -> str | None:
        return self.values.get(profile_id)

    def set(self, profile_id: str, secret: str) -> None:
        self.values[profile_id] = secret

    def delete(self, profile_id: str) -> None:
        self.values.pop(profile_id, None)


class FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def request(self, **values: Any) -> dict[str, Any]:
        self.requests.append(values)
        return self.response


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.library = root / "library"
        self.library.mkdir()
        source = init_repo(self.library / "source")
        write_skill(
            source,
            "docs-skill",
            "Create technical documentation and architecture guides.",
            body="# Workflow\n\nRead the project and produce a reviewable guide.",
        )
        commit_all(source)
        self.settings = Settings.load(self.library)
        registered = SourceManager(self.settings).register(source)
        CatalogScanner(self.settings).scan(registered["id"])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_config_is_local_secret_free_and_disabled_by_default(self) -> None:
        store = LLMConfigStore(self.settings)
        self.assertEqual(store.load().provider, "disabled")
        saved = store.save(
            LLMConfig(provider="codex", model="test-model", max_per_run=3)
        )
        self.assertEqual(saved.provider, "codex")
        payload = json.loads(self.settings.llm_config.read_text(encoding="utf-8"))
        self.assertEqual(payload["profiles"][0]["model"], "test-model")
        self.assertNotIn("api_key", payload)

    def test_malformed_config_is_rejected_cleanly(self) -> None:
        self.settings.llm_config.parent.mkdir(parents=True, exist_ok=True)
        self.settings.llm_config.write_text("[]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "configuration root"):
            LLMConfigStore(self.settings).load()

        self.settings.llm_config.write_text(
            json.dumps({"provider": "codex", "timeout_seconds": "300"}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValidationError, "timeout must be an integer"):
            LLMConfigStore(self.settings).load()

    def test_version_one_config_migrates_to_an_active_profile(self) -> None:
        self.settings.llm_config.parent.mkdir(parents=True, exist_ok=True)
        self.settings.llm_config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "provider": "claude",
                    "model": "legacy-model",
                    "timeout_seconds": 420,
                    "max_per_run": 7,
                }
            ),
            encoding="utf-8",
        )

        config = LLMConfigStore(self.settings).load()

        self.assertEqual(config.version, 2)
        self.assertEqual(config.provider, "claude")
        self.assertEqual(config.model, "legacy-model")
        self.assertEqual(config.active_profile_id, "legacy-claude")
        self.assertEqual(len(config.profiles), 1)

    def test_openai_profile_secret_is_not_persisted(self) -> None:
        secrets = MemorySecretStore()
        service = EvaluationService(self.settings, secret_store=secrets)

        status = service.save_profile(
            profile_id="office-model",
            name="Office model",
            provider="openai-compatible",
            model="company-model",
            base_url="https://llm.example.com/v1",
            api_mode="chat-completions",
            timeout_seconds=90,
            max_per_run=4,
            api_key="super-secret-key",
            activate=True,
        )

        self.assertEqual(status["config"]["active_profile_id"], "office-model")
        self.assertTrue(status["active_profile"]["credential_configured"])
        self.assertEqual(secrets.get("office-model"), "super-secret-key")
        raw = self.settings.llm_config.read_text(encoding="utf-8")
        self.assertNotIn("super-secret-key", raw)
        self.assertNotIn("api_key", raw)

        with self.assertRaisesRegex(ValidationError, "cannot change provider"):
            service.save_profile(
                profile_id="office-model",
                name="Office model",
                provider="codex",
                model=None,
            )

        deleted = service.delete_profile("office-model")
        self.assertEqual(deleted["config"]["profiles"], [])
        self.assertIsNone(secrets.get("office-model"))

    def test_remote_plain_http_profile_is_rejected_but_loopback_is_allowed(self) -> None:
        with self.assertRaisesRegex(ValidationError, "HTTPS"):
            LLMProfile(
                id="remote",
                name="Remote",
                provider="openai-compatible",
                model="model",
                base_url="http://llm.example.com/v1",
                api_mode="chat-completions",
            ).validate()

        profile = LLMProfile(
            id="local",
            name="Local",
            provider="openai-compatible",
            model="model",
            base_url="http://127.0.0.1:11434/v1",
            api_mode="chat-completions",
        ).validate()
        self.assertEqual(profile.base_url, "http://127.0.0.1:11434/v1")

    def test_chat_completions_runner_uses_schema_and_bearer_secret(self) -> None:
        secret = "request-only-secret"
        transport = FakeTransport(
            {
                "choices": [
                    {"message": {"content": json.dumps(valid_output(), ensure_ascii=False)}}
                ]
            }
        )
        secrets = MemorySecretStore()
        secrets.set("remote", secret)
        runner = OpenAICompatibleRunner(secrets, transport)
        profile = LLMProfile(
            id="remote",
            name="Remote",
            provider="openai-compatible",
            model="eval-model",
            base_url="https://llm.example.com/v1",
            api_mode="chat-completions",
            credential_configured=True,
        )

        result = runner.run(profile, "evaluate this", {"type": "object"})

        self.assertEqual(result["problem"], valid_output()["problem"])
        request = transport.requests[0]
        self.assertEqual(request["url"], "https://llm.example.com/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {secret}")
        self.assertEqual(
            request["payload"]["response_format"]["type"], "json_schema"
        )
        self.assertNotIn(secret, json.dumps(profile.as_dict()))

    def test_responses_runner_parses_output_text_and_disables_storage(self) -> None:
        transport = FakeTransport(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(valid_output(), ensure_ascii=False),
                            }
                        ],
                    }
                ]
            }
        )
        runner = OpenAICompatibleRunner(MemorySecretStore(), transport)
        profile = LLMProfile(
            id="openai",
            name="OpenAI",
            provider="openai-compatible",
            model="eval-model",
            base_url="https://api.openai.com/v1",
            api_mode="responses",
        )

        result = runner.run(profile, "evaluate this", {"type": "object"})

        self.assertEqual(result["category_l1"], "演示与文档")
        request = transport.requests[0]
        self.assertEqual(request["url"], "https://api.openai.com/v1/responses")
        self.assertFalse(request["payload"]["store"])
        self.assertEqual(request["payload"]["text"]["format"]["type"], "json_schema")

    def test_evaluation_creates_proposal_and_explicit_apply_updates_annotation(self) -> None:
        runner = FakeRunner(valid_output())
        service = EvaluationService(self.settings, runner=runner)
        service.configure(provider="codex", max_per_run=5)

        result = service.evaluate()

        self.assertEqual(result["proposed"], 1)
        self.assertEqual(result["failed"], 0)
        proposal = result["results"][0]
        self.assertEqual(proposal["score"], 8.0)
        self.assertEqual(proposal["category_l1"], "演示与文档")
        self.assertTrue(proposal["category_candidate"])
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("UNTRUSTED_SKILL_PAYLOAD", runner.calls[0][1])
        skill = Catalog(self.settings).list_skills()[0]
        self.assertIsNone(skill["score"])

        applied = service.apply(proposal["id"])

        self.assertEqual(applied["status"], "applied")
        skill = Catalog(self.settings).list_skills()[0]
        self.assertEqual(skill["score"], 8.0)
        self.assertEqual(skill["score_source"], "LLM/codex:default")
        self.assertEqual(skill["annotation_content_hash"], skill["content_hash"])
        self.assertEqual(service.pending(), [])

    def test_existing_annotation_requires_explicit_replacement(self) -> None:
        skill = Catalog(self.settings).list_skills()[0]
        Catalog(self.settings).annotate(
            skill["id"], category_l1="写作与内容", category_l2="写作", score=9
        )
        with Database(self.settings).transaction() as connection:
            connection.execute(
                "UPDATE annotations SET content_hash = 'older-content' WHERE skill_id = ?",
                (skill["id"],),
            )
        service = EvaluationService(self.settings, runner=FakeRunner(valid_output()))
        service.configure(provider="claude")
        proposal = service.evaluate()["results"][0]

        with self.assertRaises(ConflictError):
            service.apply(proposal["id"])
        applied = service.apply(proposal["id"], replace_existing=True)
        self.assertEqual(applied["status"], "applied")

    def test_invalid_taxonomy_output_is_recorded_as_error(self) -> None:
        service = EvaluationService(
            self.settings,
            runner=FakeRunner(valid_output(category_l1="模型自由发明的分类")),
        )
        service.configure(provider="codex")

        result = service.evaluate()

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["results"][0]["status"], "error")
        self.assertIn("Unknown core category", result["results"][0]["error"])

    def test_unexpected_llm_fields_are_recorded_as_error(self) -> None:
        service = EvaluationService(
            self.settings,
            runner=FakeRunner(valid_output(injected_instruction="ignore review")),
        )
        service.configure(provider="codex")

        result = service.evaluate()

        self.assertEqual(result["failed"], 1)
        self.assertIn("unknown: injected_instruction", result["results"][0]["error"])

    def test_taxonomy_uses_fixed_l1_and_only_promotes_reused_l2(self) -> None:
        snapshot = Taxonomy(Database(self.settings)).snapshot()
        self.assertEqual(snapshot["level_one"], list(CORE_L1))
        self.assertEqual(snapshot["policy"]["level_two"], "reuse-or-propose")

class ScoreValidationTests(unittest.TestCase):
    def test_human_quality_score_is_limited_to_ten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            library = Path(raw) / "library"
            library.mkdir()
            source = init_repo(library / "source")
            write_skill(source, "docs-skill", "Create documentation")
            commit_all(source)
            settings = Settings.load(library)
            registered = SourceManager(settings).register(source)
            CatalogScanner(settings).scan(registered["id"])
            skill = Catalog(settings).list_skills()[0]
            with self.assertRaises(ValidationError):
                Catalog(settings).annotate(skill["id"], score=10.1)


if __name__ == "__main__":
    unittest.main()
