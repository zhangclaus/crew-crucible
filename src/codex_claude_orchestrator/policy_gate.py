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
        blocked_destructive_command = self._blocked_destructive_command(effective_command)
        if blocked_destructive_command:
            return PolicyDecision(
                allowed=False,
                reason=f"blocked command prefix: {blocked_destructive_command}",
            )
        normalized_command = self._normalize_executable(effective_command)
        for blocked_prefix in self._blocked_command_prefixes:
            if tuple(normalized_command[: len(blocked_prefix)]) == blocked_prefix:
                return PolicyDecision(
                    allowed=False,
                    reason=f"blocked command prefix: {' '.join(blocked_prefix)}",
                )
        wrapper = self._blocked_wrapper(effective_command)
        if wrapper:
            return PolicyDecision(allowed=False, reason=f"blocked command wrapper: {wrapper}")
        return PolicyDecision(allowed=True, reason=None)

    def _effective_command(self, command: list[str]) -> list[str]:
        effective_command = command
        while effective_command and Path(effective_command[0]).name == "env":
            effective_command = self._unwrap_env_command(effective_command)
        return effective_command

    def _unwrap_env_command(self, command: list[str]) -> list[str]:
        index = 1
        while index < len(command):
            arg = command[index]
            if arg == "--":
                return command[index + 1 :]
            if not self._is_env_assignment(arg):
                return command[index:]
            index += 1
        return []

    def _normalize_executable(self, command: list[str]) -> list[str]:
        if not command:
            return command
        return [Path(command[0]).name, *command[1:]]

    def _blocked_destructive_command(self, command: list[str]) -> str | None:
        if not command:
            return None

        executable = Path(command[0]).name
        args = command[1:]
        if executable == "rm" and self._has_force_and_recursive(args):
            return "rm -rf"
        if executable == "git":
            return self._blocked_git_destructive_command(args)
        return None

    def _blocked_git_destructive_command(self, args: list[str]) -> str | None:
        if self._has_git_one_shot_config(args):
            return "git config"
        for index, arg in enumerate(args):
            remaining_args = args[index + 1 :]
            if arg == "reset" and any(self._is_git_hard_option(option) for option in remaining_args):
                return "git reset --hard"
            if arg == "clean" and self._git_clean_removes_directories(remaining_args):
                return "git clean -fd"
        return None

    def _has_git_one_shot_config(self, args: list[str]) -> bool:
        for index, arg in enumerate(args):
            if arg == "-c" and index + 1 < len(args):
                return True
            if arg.startswith("-c") and arg != "-C":
                return True
            if arg == "--config-env" and index + 1 < len(args):
                return True
            if arg.startswith("--config-env="):
                return True
        return False

    def _git_clean_removes_directories(self, args: list[str]) -> bool:
        force = False
        directories = False
        for arg in args:
            if arg == "--":
                break
            if self._is_git_force_option(arg):
                force = True
                continue
            if not arg.startswith("-") or arg == "-":
                continue
            if arg.startswith("--"):
                continue
            flags = arg[1:]
            force = force or "f" in flags
            directories = directories or "d" in flags
        return force and directories

    def _is_git_hard_option(self, arg: str) -> bool:
        option = arg.split("=", 1)[0]
        return len(option) >= len("--h") and "--hard".startswith(option)

    def _is_git_force_option(self, arg: str) -> bool:
        option = arg.split("=", 1)[0]
        return len(option) >= len("--f") and "--force".startswith(option)

    def _has_force_and_recursive(self, args: list[str]) -> bool:
        force = False
        recursive = False
        for arg in args:
            if arg == "--":
                break
            if arg.startswith("--"):
                option = arg.split("=", 1)[0]
                force = force or option == "--force"
                recursive = recursive or option == "--recursive"
                continue
            if not arg.startswith("-") or arg == "-":
                continue
            flags = arg[1:]
            force = force or "f" in flags
            recursive = recursive or "r" in flags or "R" in flags
        return force and recursive

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
        effective_command = command
        while effective_command and Path(effective_command[0]).name == "env":
            for index, arg in enumerate(effective_command[1:], start=1):
                if arg == "--":
                    effective_command = effective_command[index + 1 :]
                    break
                if self._is_env_assignment(arg):
                    continue
                if arg.startswith("-"):
                    return f"env {arg}"
                effective_command = effective_command[index:]
                break
            else:
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
