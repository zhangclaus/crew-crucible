from __future__ import annotations

from codex_claude_orchestrator.models import PolicyDecision, WorkspaceAllocation, WorkspaceMode


class PolicyGate:
    def __init__(
        self,
        protected_paths: list[str] | None = None,
        blocked_command_prefixes: list[tuple[str, ...]] | None = None,
    ):
        self._protected_paths = protected_paths or [".env", ".git/", "secrets/"]
        self._blocked_command_prefixes = blocked_command_prefixes or [
            ("rm", "-rf"),
            ("git", "reset", "--hard"),
            ("git", "clean", "-fd"),
        ]

    def guard_workspace_execution(
        self,
        allocation: WorkspaceAllocation,
        *,
        shared_write_allowed: bool = False,
    ) -> PolicyDecision:
        if allocation.mode is WorkspaceMode.SHARED and not shared_write_allowed:
            return PolicyDecision(allowed=False, reason="shared workspace execution requires explicit approval")
        return PolicyDecision(allowed=True, reason=None)

    def guard_write_targets(
        self,
        allocation: WorkspaceAllocation,
        paths: list[str],
        *,
        shared_write_allowed: bool = False,
    ) -> PolicyDecision:
        if allocation.mode is WorkspaceMode.READONLY:
            return PolicyDecision(allowed=False, reason="readonly workspace cannot be modified")
        if allocation.mode is WorkspaceMode.SHARED and not shared_write_allowed:
            return PolicyDecision(allowed=False, reason="shared workspace writes require explicit approval")

        for path in paths:
            normalized = path[2:] if path.startswith("./") else path
            if any(
                normalized == protected.rstrip("/") or normalized.startswith(protected)
                for protected in self._protected_paths
            ):
                return PolicyDecision(allowed=False, reason=f"protected path blocked: {path}")

        return PolicyDecision(allowed=True, reason=None)

    def guard_command(self, command: list[str]) -> PolicyDecision:
        for blocked_prefix in self._blocked_command_prefixes:
            if tuple(command[: len(blocked_prefix)]) == blocked_prefix:
                return PolicyDecision(
                    allowed=False,
                    reason=f"blocked command prefix: {' '.join(blocked_prefix)}",
                )
        return PolicyDecision(allowed=True, reason=None)
