from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.core.models import WorkspaceMode


@dataclass(frozen=True, slots=True)
class AgentProfile:
    name: str
    adapter: str
    description: str
    default_workspace_mode: WorkspaceMode
    readonly_tools: tuple[str, ...]
    write_tools: tuple[str, ...]
    supports_shared_workspace: bool = False
    max_concurrent_runs: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "adapter": self.adapter,
            "description": self.description,
            "default_workspace_mode": self.default_workspace_mode.value,
            "readonly_tools": list(self.readonly_tools),
            "write_tools": list(self.write_tools),
            "supports_shared_workspace": self.supports_shared_workspace,
            "max_concurrent_runs": self.max_concurrent_runs,
        }


class AgentRegistry:
    def __init__(self, profiles: list[AgentProfile]):
        self._profiles = {profile.name: profile for profile in profiles}

    @classmethod
    def default(cls) -> AgentRegistry:
        return cls(
            [
                AgentProfile(
                    name="claude",
                    adapter="claude-cli",
                    description="Claude Code worker invoked through the local CLI",
                    default_workspace_mode=WorkspaceMode.ISOLATED,
                    readonly_tools=("Read", "Glob", "Grep", "LS"),
                    write_tools=("Edit", "MultiEdit", "Write", "Bash"),
                    supports_shared_workspace=True,
                    max_concurrent_runs=1,
                )
            ]
        )

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def list_profiles(self) -> list[AgentProfile]:
        return [self._profiles[name] for name in self.names()]

    def get(self, name: str) -> AgentProfile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            known_agents = ", ".join(self.names()) or "none"
            raise KeyError(f"unknown agent {name!r}; known agents: {known_agents}") from exc

    def allowed_tools(
        self,
        name: str,
        workspace_mode: WorkspaceMode,
        *,
        shared_write_allowed: bool = False,
    ) -> list[str]:
        profile = self.get(name)
        tools = list(profile.readonly_tools)
        if workspace_mode is WorkspaceMode.READONLY:
            return tools
        if workspace_mode is WorkspaceMode.SHARED and not shared_write_allowed:
            return tools
        return tools + list(profile.write_tools)

    def build_adapter(self, name: str):
        profile = self.get(name)
        if profile.adapter == "claude-cli":
            return ClaudeCliAdapter()
        raise ValueError(f"unsupported adapter: {profile.adapter}")
