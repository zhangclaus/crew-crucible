from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from mcp.server import Server
from mcp.types import TextContent

from codex_claude_orchestrator.crew.models import (
    AuthorityLevel,
    WorkerContract,
    WorkerRole,
    WorkspacePolicy,
)


# Predefined worker templates for common roles.
WORKER_TEMPLATES: dict[str, WorkerContract] = {
    "targeted-code-editor": WorkerContract(
        contract_id="template-targeted-code-editor",
        label="targeted-code-editor",
        mission="Implement the requested changes in the source code.",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    ),
    "repo-context-scout": WorkerContract(
        contract_id="template-repo-context-scout",
        label="repo-context-scout",
        mission="Explore the codebase and report findings on the blackboard.",
        required_capabilities=["inspect_code"],
        authority_level=AuthorityLevel.READONLY,
        workspace_policy=WorkspacePolicy.READONLY,
    ),
    "patch-risk-auditor": WorkerContract(
        contract_id="template-patch-risk-auditor",
        label="patch-risk-auditor",
        mission="Review the changed files for risks and quality issues.",
        required_capabilities=["inspect_code"],
        authority_level=AuthorityLevel.READONLY,
        workspace_policy=WorkspacePolicy.READONLY,
    ),
    "verification-failure-analyst": WorkerContract(
        contract_id="template-verification-failure-analyst",
        label="verification-failure-analyst",
        mission="Analyze verification failures and propose fixes.",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    ),
    "frontend-developer": WorkerContract(
        contract_id="template-frontend-developer",
        label="frontend-developer",
        mission="Implement frontend changes (UI, components, styles, client-side logic).",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    ),
    "backend-developer": WorkerContract(
        contract_id="template-backend-developer",
        label="backend-developer",
        mission="Implement backend changes (API, services, models, database, server-side logic).",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    ),
    "test-writer": WorkerContract(
        contract_id="template-test-writer",
        label="test-writer",
        mission="Write and update tests. Do not modify source code.",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    ),
}


def register_lifecycle_tools(server: Server, controller) -> None:

    @server.tool("crew_verify")
    async def crew_verify(
        crew_id: str,
        command: str,
        worker_id: str | None = None,
    ) -> list[TextContent]:
        """Run a verification command (e.g. pytest, ruff check)."""
        try:
            result = controller.verify(crew_id=crew_id, command=command, worker_id=worker_id)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except FileNotFoundError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except ValueError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": f"internal: {exc}"}, ensure_ascii=False))]

    @server.tool("crew_spawn")
    async def crew_spawn(
        repo: str,
        crew_id: str,
        label: str,
        mission: str = "",
        write_scope: list[str] | None = None,
    ) -> list[TextContent]:
        """Spawn a worker agent. label can be a template name (targeted-code-editor, repo-context-scout, patch-risk-auditor, verification-failure-analyst, frontend-developer, backend-developer, test-writer) or a custom label. write_scope limits which files the worker can modify."""
        try:
            template = WORKER_TEMPLATES.get(label)
            overrides: dict = {"mission": mission or template.mission} if template else {}
            if write_scope is not None:
                overrides["write_scope"] = write_scope
            if template:
                contract = replace(template, **overrides)
            else:
                contract = WorkerContract(
                    contract_id=f"contract-{label}",
                    label=label,
                    mission=mission,
                    required_capabilities=["inspect_code", "edit_source"],
                    authority_level=AuthorityLevel.SOURCE_WRITE,
                    workspace_policy=WorkspacePolicy.WORKTREE,
                    **({"write_scope": write_scope} if write_scope else {}),
                )
            result = controller.ensure_worker(
                repo_root=Path(repo),
                crew_id=crew_id,
                contract=contract,
            )
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except FileNotFoundError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except ValueError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": f"internal: {exc}"}, ensure_ascii=False))]
