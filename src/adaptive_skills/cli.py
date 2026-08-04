from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .app_service import AppService
from .catalog import Catalog
from .config import Settings
from .database import Database
from .errors import AdaptiveSkillsError
from .evaluation import EvaluationService
from .inventory import InventoryBridge
from .projects import ProjectManager
from .scanner import CatalogScanner
from .source_refresh import SourceRefreshService
from .sources import SourceManager


def _emit(value: Any, *, compact: bool = False) -> None:
    print(
        json.dumps(
            value, ensure_ascii=False, indent=None if compact else 2, sort_keys=True
        )
    )


def _settings(arguments: argparse.Namespace) -> Settings:
    return Settings.load(arguments.library)


def _cmd_init(arguments: argparse.Namespace) -> dict[str, Any]:
    settings = _settings(arguments)
    with Database(settings).transaction() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    return {
        "library": str(settings.library),
        "database": str(settings.database),
        "schema_version": int(version),
    }


def _cmd_source_add(arguments: argparse.Namespace) -> dict[str, Any]:
    return SourceManager(_settings(arguments)).add(
        arguments.url, arguments.name, arguments.ref
    )


def _cmd_source_register(arguments: argparse.Namespace) -> dict[str, Any]:
    return SourceManager(_settings(arguments)).register(
        arguments.path, arguments.name, arguments.url, arguments.ref
    )


def _cmd_source_discover(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return SourceManager(_settings(arguments)).discover()


def _cmd_source_list(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return SourceManager(_settings(arguments)).list()


def _cmd_source_update(arguments: argparse.Namespace) -> Any:
    manager = SourceManager(_settings(arguments))
    if arguments.source:
        return manager.update(arguments.source)
    return [manager.update(source["id"]) for source in manager.list()]


def _cmd_source_refresh_all(arguments: argparse.Namespace) -> dict[str, Any]:
    return SourceRefreshService(_settings(arguments)).refresh_all()


def _cmd_source_policy(arguments: argparse.Namespace) -> dict[str, Any]:
    return SourceManager(_settings(arguments)).set_update_policy(
        arguments.source, arguments.policy
    )


def _cmd_scan(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return CatalogScanner(_settings(arguments)).scan(arguments.source)


def _cmd_skill_list(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return Catalog(_settings(arguments)).list_skills(include_inactive=arguments.all)


def _cmd_skill_show(arguments: argparse.Namespace) -> dict[str, Any]:
    return Catalog(_settings(arguments)).get_skill(
        arguments.skill, active_only=not arguments.all
    )


def _cmd_search(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return Catalog(_settings(arguments)).search(
        arguments.requirement,
        limit=arguments.limit,
        include_invalid=arguments.include_invalid,
        allow_risk=arguments.allow_risk,
    )


def _cmd_annotate(arguments: argparse.Namespace) -> dict[str, Any]:
    values = {
        "category_l1": arguments.category_l1,
        "category_l2": arguments.category_l2,
        "problem": arguments.problem,
        "use_case": arguments.use_case,
        "score": arguments.score,
        "score_source": arguments.score_source,
        "notes": arguments.notes,
        "tags": arguments.tags,
        "review_status": arguments.review_status,
    }
    return Catalog(_settings(arguments)).annotate(
        arguments.skill,
        **{key: value for key, value in values.items() if value is not None},
    )


def _cmd_llm_status(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).status()


def _cmd_llm_config_show(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).status()


def _cmd_llm_config_set(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).configure(
        provider=arguments.provider,
        model=arguments.model,
        timeout_seconds=arguments.timeout,
        max_per_run=arguments.max_per_run,
    )


def _cmd_llm_profile_list(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return EvaluationService(_settings(arguments)).status()["config"]["profiles"]


def _cmd_llm_profile_save(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).save_profile(
        profile_id=arguments.profile_id,
        name=arguments.name,
        provider=arguments.provider,
        model=arguments.model,
        base_url=arguments.base_url,
        api_mode=arguments.api_mode,
        timeout_seconds=arguments.timeout,
        max_per_run=arguments.max_per_run,
        api_key=os.environ.get("ADAPTIVE_SKILLS_LLM_PROFILE_SECRET"),
        activate=arguments.activate,
    )


def _cmd_llm_profile_activate(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).activate_profile(
        arguments.profile_id
    )


def _cmd_llm_profile_delete(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).delete_profile(arguments.profile_id)


def _cmd_llm_profile_test(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).test_profile(arguments.profile_id)


def _cmd_llm_pending(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return EvaluationService(_settings(arguments)).pending(
        source=arguments.source, limit=arguments.limit
    )


def _cmd_llm_evaluate(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).evaluate(
        source=arguments.source, limit=arguments.limit
    )


def _cmd_llm_list(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return EvaluationService(_settings(arguments)).list(
        status=arguments.status, limit=arguments.limit
    )


def _cmd_llm_apply(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).apply(
        arguments.evaluation, replace_existing=arguments.replace_existing
    )


def _cmd_llm_reject(arguments: argparse.Namespace) -> dict[str, Any]:
    return EvaluationService(_settings(arguments)).reject(arguments.evaluation)


def _cmd_project_plan(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).plan(
        arguments.project,
        arguments.requirement,
        limit=arguments.limit,
        target=arguments.target,
        allow_risk=arguments.allow_risk,
    )


def _cmd_project_apply(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).apply(
        arguments.project,
        arguments.skill,
        target=arguments.target,
        mode=arguments.mode,
        requirement=arguments.requirement,
        allow_risk=arguments.allow_risk,
    )


def _cmd_project_status(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).status(arguments.project)


def _cmd_project_history(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).history(
        arguments.project, limit=arguments.limit
    )


def _cmd_project_sync(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).sync(
        arguments.project, force=arguments.force, allow_risk=arguments.allow_risk
    )


def _cmd_project_unlink(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).unlink(
        arguments.project, skill_ids=arguments.skill, force=arguments.force
    )


def _cmd_project_list(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    return ProjectManager(_settings(arguments)).list_projects()


def _cmd_project_register(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).register(arguments.project)


def _cmd_project_forget(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).forget(arguments.project)


def _cmd_project_relink(arguments: argparse.Namespace) -> dict[str, Any]:
    return ProjectManager(_settings(arguments)).relink(
        arguments.project_id, arguments.new_path
    )


def _cmd_inventory_import(arguments: argparse.Namespace) -> dict[str, Any]:
    return InventoryBridge(_settings(arguments)).import_xlsx(
        arguments.workbook, sheet=arguments.sheet
    )


def _cmd_inventory_export(arguments: argparse.Namespace) -> dict[str, Any]:
    return InventoryBridge(_settings(arguments)).export_xlsx(
        arguments.output, template=arguments.template
    )


def _cmd_app_snapshot(arguments: argparse.Namespace) -> dict[str, Any]:
    return AppService(_settings(arguments)).snapshot(
        query=arguments.query, limit=arguments.limit
    )


def _handler(
    parser: argparse.ArgumentParser, function: Callable[[argparse.Namespace], Any]
) -> None:
    parser.set_defaults(handler=function)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adaptive-skills",
        description="Manage a curated library of Agent Skills and link only selected skills into projects.",
    )
    parser.add_argument(
        "--library",
        help="Skill library root (default: ADAPTIVE_SKILLS_LIBRARY or ~/skills)",
    )
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize the SQLite catalog")
    _handler(init, _cmd_init)

    source = commands.add_parser("source", help="Manage Git skill sources")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_add = source_commands.add_parser("add", help="Clone and register a Git URL")
    source_add.add_argument("url")
    source_add.add_argument("--name")
    source_add.add_argument("--ref", help="Branch or ref to track")
    _handler(source_add, _cmd_source_add)
    source_register = source_commands.add_parser(
        "register", help="Register an existing local Git repository"
    )
    source_register.add_argument("path", type=Path)
    source_register.add_argument("--name")
    source_register.add_argument("--url")
    source_register.add_argument("--ref")
    _handler(source_register, _cmd_source_register)
    source_discover = source_commands.add_parser(
        "discover", help="Register top-level Git repositories in the library"
    )
    _handler(source_discover, _cmd_source_discover)
    source_list = source_commands.add_parser("list", help="List registered sources")
    _handler(source_list, _cmd_source_list)
    source_update = source_commands.add_parser(
        "update", help="Fast-forward one source, or all sources"
    )
    source_update.add_argument("source", nargs="?")
    _handler(source_update, _cmd_source_update)
    source_refresh_all = source_commands.add_parser(
        "refresh-all", help="Fast-forward and rescan every source, continuing on errors"
    )
    _handler(source_refresh_all, _cmd_source_refresh_all)
    source_policy = source_commands.add_parser(
        "policy", help="Choose remote-following or local-maintained source behavior"
    )
    source_policy.add_argument("source")
    source_policy.add_argument("policy", choices=["remote", "local"])
    _handler(source_policy, _cmd_source_policy)

    scan = commands.add_parser(
        "scan", help="Scan one source, or every registered source"
    )
    scan.add_argument("source", nargs="?")
    _handler(scan, _cmd_scan)

    skill = commands.add_parser("skill", help="Inspect catalog skills")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_list = skill_commands.add_parser("list")
    skill_list.add_argument(
        "--all", action="store_true", help="Include inactive skills"
    )
    _handler(skill_list, _cmd_skill_list)
    skill_show = skill_commands.add_parser("show")
    skill_show.add_argument("skill")
    skill_show.add_argument("--all", action="store_true", help="Allow inactive skills")
    _handler(skill_show, _cmd_skill_show)

    search = commands.add_parser(
        "search", help="Find skills for a natural-language requirement"
    )
    search.add_argument("requirement")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-invalid", action="store_true")
    search.add_argument("--allow-risk", action="store_true")
    _handler(search, _cmd_search)

    annotate = commands.add_parser(
        "annotate", help="Update human curation fields for a stable skill ID"
    )
    annotate.add_argument("skill")
    annotate.add_argument("--category-l1")
    annotate.add_argument("--category-l2")
    annotate.add_argument("--problem")
    annotate.add_argument("--use-case")
    annotate.add_argument("--score", type=float)
    annotate.add_argument("--score-source")
    annotate.add_argument("--notes")
    annotate.add_argument("--tags", help="Comma-delimited tags")
    annotate.add_argument("--review-status")
    _handler(annotate, _cmd_annotate)

    llm = commands.add_parser(
        "llm", help="Configure and run optional LLM classification proposals"
    )
    llm_commands = llm.add_subparsers(dest="llm_command", required=True)
    llm_status = llm_commands.add_parser("status")
    _handler(llm_status, _cmd_llm_status)
    llm_config = llm_commands.add_parser("config")
    llm_config_commands = llm_config.add_subparsers(
        dest="llm_config_command", required=True
    )
    llm_config_show = llm_config_commands.add_parser("show")
    _handler(llm_config_show, _cmd_llm_config_show)
    llm_config_set = llm_config_commands.add_parser("set")
    llm_config_set.add_argument(
        "--provider", choices=["disabled", "codex", "claude"], required=True
    )
    llm_config_set.add_argument("--model")
    llm_config_set.add_argument("--timeout", type=int, default=300)
    llm_config_set.add_argument("--max-per-run", type=int, default=20)
    _handler(llm_config_set, _cmd_llm_config_set)
    llm_profile = llm_commands.add_parser("profile", help="Manage LLM profiles")
    llm_profile_commands = llm_profile.add_subparsers(
        dest="llm_profile_command", required=True
    )
    llm_profile_list = llm_profile_commands.add_parser("list")
    _handler(llm_profile_list, _cmd_llm_profile_list)
    llm_profile_save = llm_profile_commands.add_parser("save")
    llm_profile_save.add_argument("--id", dest="profile_id", required=True)
    llm_profile_save.add_argument("--name", required=True)
    llm_profile_save.add_argument(
        "--provider", choices=["codex", "claude", "openai-compatible"], required=True
    )
    llm_profile_save.add_argument("--model")
    llm_profile_save.add_argument("--base-url")
    llm_profile_save.add_argument(
        "--api-mode", choices=["responses", "chat-completions", "auto"]
    )
    llm_profile_save.add_argument("--timeout", type=int, default=300)
    llm_profile_save.add_argument("--max-per-run", type=int, default=20)
    llm_profile_save.add_argument(
        "--no-activate", dest="activate", action="store_false"
    )
    llm_profile_save.set_defaults(activate=True)
    _handler(llm_profile_save, _cmd_llm_profile_save)
    llm_profile_activate = llm_profile_commands.add_parser("activate")
    llm_profile_activate.add_argument("profile_id")
    _handler(llm_profile_activate, _cmd_llm_profile_activate)
    llm_profile_delete = llm_profile_commands.add_parser("delete")
    llm_profile_delete.add_argument("profile_id")
    _handler(llm_profile_delete, _cmd_llm_profile_delete)
    llm_profile_test = llm_profile_commands.add_parser("test")
    llm_profile_test.add_argument("profile_id")
    _handler(llm_profile_test, _cmd_llm_profile_test)
    llm_pending = llm_commands.add_parser("pending")
    llm_pending.add_argument("--source")
    llm_pending.add_argument("--limit", type=int, default=100)
    _handler(llm_pending, _cmd_llm_pending)
    llm_evaluate = llm_commands.add_parser("evaluate")
    llm_evaluate.add_argument("--source")
    llm_evaluate.add_argument("--limit", type=int)
    _handler(llm_evaluate, _cmd_llm_evaluate)
    llm_list = llm_commands.add_parser("list")
    llm_list.add_argument(
        "--status", choices=["proposed", "applied", "rejected", "error"]
    )
    llm_list.add_argument("--limit", type=int, default=100)
    _handler(llm_list, _cmd_llm_list)
    llm_apply = llm_commands.add_parser("apply")
    llm_apply.add_argument("evaluation")
    llm_apply.add_argument("--replace-existing", action="store_true")
    _handler(llm_apply, _cmd_llm_apply)
    llm_reject = llm_commands.add_parser("reject")
    llm_reject.add_argument("evaluation")
    _handler(llm_reject, _cmd_llm_reject)

    project = commands.add_parser(
        "project", help="Manage project-scoped skill references"
    )
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_list = project_commands.add_parser(
        "list", help="List projects remembered by this library"
    )
    _handler(project_list, _cmd_project_list)
    project_register = project_commands.add_parser(
        "register", help="Remember an existing manifest-managed project"
    )
    project_register.add_argument("project", type=Path)
    _handler(project_register, _cmd_project_register)
    project_forget = project_commands.add_parser(
        "forget", help="Forget a project without changing its manifest"
    )
    project_forget.add_argument("project")
    _handler(project_forget, _cmd_project_forget)
    project_relink = project_commands.add_parser(
        "relink", help="Point a missing project record at its new directory"
    )
    project_relink.add_argument("project_id")
    project_relink.add_argument("new_path", type=Path)
    _handler(project_relink, _cmd_project_relink)
    project_plan = project_commands.add_parser(
        "plan", help="Recommend skills without changing the project"
    )
    project_plan.add_argument("project", type=Path)
    project_plan.add_argument("--requirement", required=True)
    project_plan.add_argument("--limit", type=int, default=5)
    project_plan.add_argument(
        "--target", choices=["auto", "universal", "codex", "claude"], default="auto"
    )
    project_plan.add_argument("--allow-risk", action="store_true")
    _handler(project_plan, _cmd_project_plan)
    project_apply = project_commands.add_parser(
        "apply", help="Install selected catalog skills into a project"
    )
    project_apply.add_argument("project", type=Path)
    project_apply.add_argument("--skill", action="append", required=True)
    project_apply.add_argument(
        "--target", choices=["auto", "universal", "codex", "claude"], default="auto"
    )
    project_apply.add_argument(
        "--mode", choices=["auto", "symlink", "copy"], default="auto"
    )
    project_apply.add_argument("--requirement")
    project_apply.add_argument("--allow-risk", action="store_true")
    _handler(project_apply, _cmd_project_apply)
    project_status = project_commands.add_parser(
        "status", help="Detect missing, replaced, or drifting entries"
    )
    project_status.add_argument("project", type=Path)
    _handler(project_status, _cmd_project_status)
    project_history = project_commands.add_parser(
        "history", help="List successful manifest-managed project operations"
    )
    project_history.add_argument("project", type=Path)
    project_history.add_argument("--limit", type=int, default=50)
    _handler(project_history, _cmd_project_history)
    project_sync = project_commands.add_parser(
        "sync", help="Synchronize managed entries with their catalog source"
    )
    project_sync.add_argument("project", type=Path)
    project_sync.add_argument(
        "--force", action="store_true", help="Overwrite a changed managed copy"
    )
    project_sync.add_argument(
        "--allow-risk",
        action="store_true",
        help="Sync a previously accepted high-risk skill",
    )
    _handler(project_sync, _cmd_project_sync)
    project_unlink = project_commands.add_parser(
        "unlink", help="Remove selected managed entries, or all entries"
    )
    project_unlink.add_argument("project", type=Path)
    project_unlink.add_argument("--skill", action="append")
    project_unlink.add_argument(
        "--force", action="store_true", help="Remove a changed managed entry"
    )
    _handler(project_unlink, _cmd_project_unlink)

    inventory = commands.add_parser(
        "inventory", help="Import or export the optional Excel curation surface"
    )
    inventory_commands = inventory.add_subparsers(
        dest="inventory_command", required=True
    )
    inventory_import = inventory_commands.add_parser("import-xlsx")
    inventory_import.add_argument("workbook", type=Path)
    inventory_import.add_argument("--sheet", default="技能总表")
    _handler(inventory_import, _cmd_inventory_import)
    inventory_export = inventory_commands.add_parser("export")
    inventory_export.add_argument("--output", type=Path, required=True)
    inventory_export.add_argument("--template", type=Path)
    _handler(inventory_export, _cmd_inventory_export)

    app = commands.add_parser(
        "app", help="Versioned read models for the local desktop application"
    )
    app_commands = app.add_subparsers(dest="app_command", required=True)
    app_snapshot = app_commands.add_parser(
        "snapshot", help="Return dashboard, source, filter, and compact skill data"
    )
    app_snapshot.add_argument("--query")
    app_snapshot.add_argument("--limit", type=int, default=500)
    _handler(app_snapshot, _cmd_app_snapshot)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        result = arguments.handler(arguments)
    except AdaptiveSkillsError as exc:
        print(
            json.dumps(
                {"error": str(exc), "type": exc.__class__.__name__}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print(
            json.dumps({"error": "Interrupted", "type": "KeyboardInterrupt"}),
            file=sys.stderr,
        )
        return 130
    _emit(result, compact=arguments.compact)
    return 0
