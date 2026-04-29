from __future__ import annotations

from pathlib import Path

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
        if not paths:
            return PolicyDecision(allowed=True, reason=None)
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
        env_wrapper = self._blocked_env_option_wrapper(command)
        if env_wrapper:
            return PolicyDecision(allowed=False, reason=f"blocked command wrapper: {env_wrapper}")

        effective_command = self._effective_command(command)
        for blocked_prefix in self._blocked_command_prefixes:
            if tuple(effective_command[: len(blocked_prefix)]) == blocked_prefix:
                return PolicyDecision(
                    allowed=False,
                    reason=f"blocked command prefix: {' '.join(blocked_prefix)}",
                )
        wrapper = self._blocked_wrapper(effective_command)
        if wrapper:
            return PolicyDecision(allowed=False, reason=f"blocked command wrapper: {wrapper}")
        return PolicyDecision(allowed=True, reason=None)

    def _effective_command(self, command: list[str]) -> list[str]:
        if not command or Path(command[0]).name != "env":
            return command

        index = 1
        while index < len(command):
            arg = command[index]
            if arg == "--":
                index += 1
                break
            if arg in {"-S", "--split-string"} or arg.startswith("--split-string="):
                return ["env", "-S"]
            if arg in {"-i", "--ignore-environment"}:
                index += 1
                continue
            if arg in {"-u", "--unset"}:
                index += 2
                continue
            if arg.startswith("--unset="):
                index += 1
                continue
            if not self._is_env_assignment(arg):
                break
            index += 1
        return command[index:]

    def _blocked_wrapper(self, command: list[str]) -> str | None:
        if len(command) < 2:
            return None

        executable = Path(command[0]).name
        args = command[1:]
        if executable == "env" and args[:1] == ["-S"]:
            return "env -S"
        if executable in {"sh", "bash", "zsh"}:
            for arg in args:
                if self._is_shell_inline_flag(arg):
                    return f"{executable} {arg}"
        if self._is_python_executable(executable) and "-c" in args:
            return f"{executable} -c"
        if executable == "node":
            for arg in args:
                if self._is_node_inline_flag(arg):
                    return f"{executable} {arg}"
        if executable in {"ruby", "perl"} and "-e" in args:
            return f"{executable} -e"
        return None

    def _blocked_env_option_wrapper(self, command: list[str]) -> str | None:
        if not command or Path(command[0]).name != "env":
            return None

        for arg in command[1:]:
            if arg == "--":
                return None
            if self._is_env_assignment(arg):
                continue
            if arg.startswith("-"):
                return f"env {arg}"
            return None
        return None

    def _is_shell_inline_flag(self, arg: str) -> bool:
        if arg in {"-c", "--command"}:
            return True
        if not arg.startswith("-") or arg.startswith("--"):
            return False
        return "c" in arg[1:]

    def _is_node_inline_flag(self, arg: str) -> bool:
        if arg in {"-e", "-p", "--eval", "--print"}:
            return True
        if arg.startswith("--eval=") or arg.startswith("--print="):
            return True
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
            return "e" in arg[1:] or "p" in arg[1:]
        return False

    def _is_env_assignment(self, arg: str) -> bool:
        name, separator, _value = arg.partition("=")
        if not separator or not name:
            return False
        if not (name[0].isalpha() or name[0] == "_"):
            return False
        return all(character.isalnum() or character == "_" for character in name)

    def _is_python_executable(self, executable: str) -> bool:
        if executable in {"python", "python3"}:
            return True
        if not executable.startswith("python3."):
            return False
        return executable.removeprefix("python3.").isdigit()
