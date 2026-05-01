from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


Sleep = Callable[[float], None]


class BridgeSupervisorLoop:
    def __init__(self, bridge, *, sleep: Sleep | None = None):
        self._bridge = bridge
        self._sleep = sleep or time.sleep

    def run(
        self,
        *,
        repo_root: Path,
        goal: str,
        workspace_mode: str,
        visual: str,
        verification_commands: list[str],
        max_rounds: int,
        poll_interval_seconds: float = 5.0,
        max_wait_cycles: int | None = None,
    ) -> dict[str, Any]:
        self._require_verification_commands(verification_commands)
        start_result = self._bridge.start(
            repo_root=repo_root,
            goal=goal,
            workspace_mode=workspace_mode,
            visual=visual,
            supervised=True,
        )
        bridge_id = str(start_result["bridge"]["bridge_id"])
        result = self.supervise(
            repo_root=repo_root,
            bridge_id=bridge_id,
            verification_commands=verification_commands,
            max_rounds=max_rounds,
            poll_interval_seconds=poll_interval_seconds,
            max_wait_cycles=max_wait_cycles,
        )
        result["start"] = start_result
        return result

    def supervise(
        self,
        *,
        repo_root: Path,
        bridge_id: str | None,
        verification_commands: list[str],
        max_rounds: int,
        poll_interval_seconds: float = 5.0,
        max_wait_cycles: int | None = None,
    ) -> dict[str, Any]:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        self._require_verification_commands(verification_commands)

        events: list[dict[str, Any]] = []
        processed_turn_ids: set[str] = set()
        rounds_used = 0
        wait_cycles = 0

        while True:
            snapshot = self._bridge.status(repo_root=repo_root, bridge_id=bridge_id)
            bridge = snapshot["bridge"]
            resolved_bridge_id = str(bridge["bridge_id"])
            self._require_supervised_snapshot(snapshot)
            terminal = self._terminal_result(snapshot, rounds_used, events)
            if terminal:
                return terminal

            latest_turn = snapshot.get("latest_turn")
            latest_turn_id = str(bridge.get("latest_turn_id") or "")
            if not latest_turn or not latest_turn_id or latest_turn_id in processed_turn_ids:
                if max_wait_cycles is not None and wait_cycles >= max_wait_cycles:
                    return self._mark_needs_human(
                        repo_root=repo_root,
                        bridge_id=resolved_bridge_id,
                        summary="Codex auto-supervisor timed out waiting for a new Claude turn.",
                        rounds_used=rounds_used,
                        events=events,
                    )
                wait_cycles += 1
                events.append({"action": "wait", "bridge_id": resolved_bridge_id})
                self._sleep(poll_interval_seconds)
                continue

            wait_cycles = 0
            processed_turn_ids.add(latest_turn_id)
            rounds_used += 1

            failed_verification = self._run_verifications(
                repo_root=repo_root,
                bridge_id=resolved_bridge_id,
                turn_id=latest_turn_id,
                verification_commands=verification_commands,
                events=events,
            )
            if failed_verification is None:
                accepted = self._bridge.accept(
                    repo_root=repo_root,
                    bridge_id=resolved_bridge_id,
                    summary="Codex auto-supervisor accepted after verification passed.",
                )
                events.append({"action": "accept", "bridge_id": resolved_bridge_id})
                return self._result(
                    snapshot={"bridge": accepted["bridge"]},
                    rounds_used=rounds_used,
                    events=events,
                )

            if rounds_used >= max_rounds:
                summary = (
                    "Codex auto-supervisor round budget exhausted after failed verification: "
                    f"{failed_verification['summary']}"
                )
                return self._mark_needs_human(
                    repo_root=repo_root,
                    bridge_id=resolved_bridge_id,
                    summary=summary,
                    rounds_used=rounds_used,
                    events=events,
                )

            repair_goal = self._repair_goal(failed_verification)
            challenge = self._bridge.challenge(
                repo_root=repo_root,
                bridge_id=resolved_bridge_id,
                summary=f"Verification failed: {failed_verification['summary']}",
                repair_goal=repair_goal,
                send=True,
            )
            events.append(
                {
                    "action": "challenge",
                    "bridge_id": resolved_bridge_id,
                    "challenge_id": challenge["challenge"]["challenge_id"],
                    "repair_goal": repair_goal,
                }
            )
            if challenge.get("latest_turn") is None:
                return self._mark_needs_human(
                    repo_root=repo_root,
                    bridge_id=resolved_bridge_id,
                    summary=f"Codex auto-supervisor challenge send failed: {challenge.get('send_error')}",
                    rounds_used=rounds_used,
                    events=events,
                )

    def _run_verifications(
        self,
        *,
        repo_root: Path,
        bridge_id: str,
        turn_id: str,
        verification_commands: list[str],
        events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for command in verification_commands:
            verification_result = self._bridge.verify(
                repo_root=repo_root,
                bridge_id=bridge_id,
                turn_id=turn_id,
                command=command,
            )
            verification = verification_result["verification"]
            events.append(
                {
                    "action": "verify",
                    "bridge_id": bridge_id,
                    "turn_id": turn_id,
                    "command": command,
                    "passed": bool(verification["passed"]),
                    "summary": verification["summary"],
                }
            )
            if not verification["passed"]:
                return verification
        return None

    def _mark_needs_human(
        self,
        *,
        repo_root: Path,
        bridge_id: str,
        summary: str,
        rounds_used: int,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        needs_human = self._bridge.needs_human(repo_root=repo_root, bridge_id=bridge_id, summary=summary)
        events.append({"action": "needs_human", "bridge_id": bridge_id, "summary": summary})
        return self._result(
            snapshot={"bridge": needs_human["bridge"]},
            rounds_used=rounds_used,
            events=events,
        )

    def _require_verification_commands(self, verification_commands: list[str]) -> None:
        if not any(command.strip() for command in verification_commands):
            raise ValueError("at least one verification command is required for bridge auto-supervision")

    def _require_supervised_snapshot(self, snapshot: dict[str, Any]) -> None:
        bridge = snapshot["bridge"]
        if not bridge.get("supervised") or not bridge.get("session_id"):
            raise ValueError(f"bridge {bridge['bridge_id']} is not supervised")

    def _terminal_result(
        self,
        snapshot: dict[str, Any],
        rounds_used: int,
        events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        bridge = snapshot["bridge"]
        status = str(bridge.get("status") or "")
        session = snapshot.get("session") or {}
        session_status = str(session.get("status") or "")
        if status in {"accepted", "needs_human", "failed"} or session_status in {
            "accepted",
            "needs_human",
            "failed",
            "blocked",
        }:
            events.append({"action": "terminal", "bridge_id": bridge["bridge_id"], "status": status})
            return self._result(snapshot=snapshot, rounds_used=rounds_used, events=events)
        return None

    def _result(
        self,
        *,
        snapshot: dict[str, Any],
        rounds_used: int,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        bridge = snapshot["bridge"]
        status = str(bridge.get("status") or "")
        return {
            "bridge_id": bridge["bridge_id"],
            "status": status,
            "rounds_used": rounds_used,
            "accepted": status == "accepted",
            "needs_human": status == "needs_human",
            "events": events,
        }

    def _repair_goal(self, verification: dict[str, Any]) -> str:
        command = verification.get("command") or ""
        summary = verification.get("summary") or "verification failed"
        return (
            "Codex verification failed. Repair the implementation, preserve unrelated user work, "
            f"then report the fix and evidence. Verification command: {command}. Failure: {summary}"
        )
