from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from codex_claude_orchestrator.core.policy_gate import PolicyGate


def run_verified_command(
    command: str,
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run a verification command with PolicyGate protection.

    - Validates command against PolicyGate blocklist
    - Splits with shlex (no shell=True)
    - Returns CompletedProcess

    Raises PolicyViolationError if the command is blocked.
    """
    argv = shlex.split(command)
    PolicyGate().guard_command(argv)
    return subprocess.run(
        argv,
        shell=False,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
