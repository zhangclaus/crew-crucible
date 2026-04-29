from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from typing import Any
from uuid import uuid4

from codex_claude_orchestrator.models import (
    ChallengeRecord,
    ChallengeType,
    EvaluationOutcome,
    OutputTrace,
    SessionRecord,
    SessionStatus,
    TurnPhase,
    TurnRecord,
    VerificationRecord,
    WorkerResult,
    WorkspaceMode,
    utc_now,
)
from codex_claude_orchestrator.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.session_recorder import SessionRecorder


CommandRunner = Callable[..., CompletedProcess[str]]
_TERMINAL_BRIDGE_STATUSES = {"accepted", "needs_human"}
_TERMINAL_SESSION_STATUSES = {"accepted", "needs_human", "failed", "blocked"}


class ClaudeBridge:
    def __init__(
        self,
        state_root: Path,
        *,
        runner: CommandRunner | None = None,
        visual_runner: CommandRunner | None = None,
        session_recorder: SessionRecorder | None = None,
        verification_runner: Any | None = None,
        result_evaluator: ResultEvaluator | None = None,
        bridge_id_factory: Callable[[], str] | None = None,
        turn_id_factory: Callable[[], str] | None = None,
        session_id_factory: Callable[[], str] | None = None,
        task_id_factory: Callable[[], str] | None = None,
        trace_id_factory: Callable[[], str] | None = None,
        challenge_id_factory: Callable[[], str] | None = None,
    ):
        self._state_root = state_root
        self._bridges_root = state_root / "claude-bridge"
        self._bridges_root.mkdir(parents=True, exist_ok=True)
        self._runner = runner or subprocess.run
        self._visual_runner = visual_runner or subprocess.run
        self._session_recorder = session_recorder or SessionRecorder(state_root)
        self._verification_runner = verification_runner
        self._result_evaluator = result_evaluator or ResultEvaluator()
        self._bridge_id_factory = bridge_id_factory or (lambda: f"bridge-{uuid4().hex}")
        self._turn_id_factory = turn_id_factory or (lambda: f"turn-{uuid4().hex}")
        self._session_id_factory = session_id_factory or (lambda: f"session-{uuid4().hex}")
        self._task_id_factory = task_id_factory or (lambda: f"task-{uuid4().hex}")
        self._trace_id_factory = trace_id_factory or (lambda: f"trace-{uuid4().hex}")
        self._challenge_id_factory = challenge_id_factory or (lambda: f"challenge-{uuid4().hex}")

    def start(
        self,
        *,
        repo_root: Path,
        goal: str,
        workspace_mode: str = "readonly",
        visual: str = "none",
        dry_run: bool = False,
        supervised: bool = False,
    ) -> dict[str, Any]:
        repo = self._resolve_repo(repo_root)
        bridge_id = self._bridge_id_factory()
        created_at = utc_now()
        record = {
            "bridge_id": bridge_id,
            "repo": str(repo),
            "goal": goal,
            "workspace_mode": workspace_mode,
            "status": "created",
            "claude_session_id": None,
            "turn_count": 0,
            "created_at": created_at,
            "updated_at": created_at,
        }
        bridge_dir = self._bridge_dir(bridge_id)
        bridge_dir.mkdir(parents=True, exist_ok=False)
        self._write_record(bridge_id, record)
        self._initialize_log(bridge_id, record)
        self._write_latest(bridge_id)
        visual_result = self._start_visual(bridge_id=bridge_id, mode=visual, dry_run=dry_run)
        if supervised:
            supervised_session = self._create_supervised_session(repo, goal, workspace_mode)
            record.update(
                {
                    "supervised": True,
                    "session_id": supervised_session.session_id,
                    "root_task_id": supervised_session.root_task_id,
                    "latest_turn_id": None,
                    "latest_verification_status": None,
                    "latest_challenge_id": None,
                }
            )
            self._write_record(bridge_id, record)
        if not dry_run:
            record = self._mark_record_running(record)
            self._write_record(bridge_id, record)
            self._append_log_status(bridge_id, "Claude start turn running")

        turn = self._run_turn(
            repo=repo,
            bridge_id=bridge_id,
            turn_kind="start",
            message=self._render_start_prompt(repo, goal, workspace_mode),
            workspace_mode=workspace_mode,
            resume_session_id=None,
            dry_run=dry_run,
        )
        record = self._advance_record(record, turn, dry_run=dry_run)
        if record.get("supervised"):
            self._mirror_bridge_turn(record, turn)
        self._write_record(bridge_id, record)
        return {"bridge": record, "latest_turn": turn, "visual": visual_result}

    def send(
        self,
        *,
        repo_root: Path,
        bridge_id: str | None,
        message: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        repo = self._resolve_repo(repo_root)
        resolved_bridge_id = self._resolve_bridge_id(bridge_id)
        record = self._read_record(resolved_bridge_id)
        self._require_not_finalized(record)
        resume_session_id = record.get("claude_session_id")
        if not resume_session_id and not dry_run:
            raise ValueError(f"bridge {resolved_bridge_id} has no Claude session id")

        turn = self._run_turn(
            repo=repo,
            bridge_id=resolved_bridge_id,
            turn_kind="send",
            message=message,
            workspace_mode=str(record["workspace_mode"]),
            resume_session_id=str(resume_session_id) if resume_session_id else None,
            dry_run=dry_run,
        )
        record = self._advance_record(record, turn, dry_run=dry_run)
        if record.get("supervised"):
            self._mirror_bridge_turn(record, turn)
        self._write_record(resolved_bridge_id, record)
        self._write_latest(resolved_bridge_id)
        return {"bridge": record, "latest_turn": turn}

    def tail(self, *, repo_root: Path, bridge_id: str | None, limit: int = 5) -> dict[str, Any]:
        self._resolve_repo(repo_root)
        resolved_bridge_id = self._resolve_bridge_id(bridge_id)
        record = self._read_record(resolved_bridge_id)
        turns = self._read_turns(resolved_bridge_id)
        if limit >= 0:
            turns = turns[-limit:] if limit else []
        return {"bridge": record, "turns": turns}

    def status(self, *, repo_root: Path, bridge_id: str | None) -> dict[str, Any]:
        self._resolve_repo(repo_root)
        resolved_bridge_id = self._resolve_bridge_id(bridge_id)
        record = self._read_record(resolved_bridge_id)
        turns = self._read_turns(resolved_bridge_id)
        session_payload = None
        latest_verification = None
        latest_challenge = None

        if record.get("session_id"):
            session_payload = self._session_recorder.read_session(str(record["session_id"]))
            verifications = session_payload["verifications"]
            challenges = session_payload["challenges"]
            latest_verification = verifications[-1] if verifications else None
            latest_challenge = challenges[-1] if challenges else None

        return {
            "bridge": record,
            "session": session_payload["session"] if session_payload else None,
            "latest_turn": turns[-1] if turns else None,
            "latest_verification": latest_verification,
            "latest_challenge": latest_challenge,
            "suggested_next": self._suggest_next(record, latest_verification, latest_challenge),
        }

    def verify(
        self,
        *,
        repo_root: Path,
        bridge_id: str | None,
        command: str,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        self._resolve_repo(repo_root)
        resolved_bridge_id = self._resolve_bridge_id(bridge_id)
        record = self._read_record(resolved_bridge_id)
        self._require_supervised(record)
        self._require_not_finalized(record)
        if self._verification_runner is None:
            raise ValueError("supervised bridge verification runner is not configured")

        resolved_turn_id = turn_id or str(record.get("latest_turn_id") or "")
        if not resolved_turn_id:
            raise ValueError(f"bridge {resolved_bridge_id} has no turn to verify")
        self._require_bridge_turn(resolved_bridge_id, resolved_turn_id)

        verification = self._verification_runner.run(str(record["session_id"]), resolved_turn_id, command)
        self._append_verification_turn(record, verification)
        updated = dict(record)
        updated["latest_verification_status"] = "passed" if verification.passed else "failed"
        updated["updated_at"] = verification.created_at
        self._write_record(resolved_bridge_id, updated)
        self._append_log_verification(resolved_bridge_id, verification)
        return {"bridge": updated, "verification": verification.to_dict()}

    def challenge(
        self,
        *,
        repo_root: Path,
        bridge_id: str | None,
        summary: str,
        repair_goal: str,
        send: bool = False,
    ) -> dict[str, Any]:
        repo = self._resolve_repo(repo_root)
        resolved_bridge_id = self._resolve_bridge_id(bridge_id)
        record = self._read_record(resolved_bridge_id)
        self._require_supervised(record)
        self._require_not_finalized(record)
        latest_turn_id = str(record.get("latest_turn_id") or "")
        if not latest_turn_id:
            raise ValueError(f"bridge {resolved_bridge_id} has no turn to challenge")
        self._require_bridge_turn(resolved_bridge_id, latest_turn_id)

        challenge = ChallengeRecord(
            challenge_id=self._challenge_id_factory(),
            session_id=str(record["session_id"]),
            turn_id=latest_turn_id,
            round_index=1,
            challenge_type=ChallengeType.QUALITY_RISK,
            summary=summary,
            question="What repair is needed for Codex to accept this bridge turn?",
            expected_evidence="Claude should provide repaired work and verification evidence.",
            severity=2,
            evidence={"bridge_id": resolved_bridge_id, "turn_id": latest_turn_id},
            repair_goal=repair_goal,
        )
        self._session_recorder.append_challenge(str(record["session_id"]), challenge)
        self._append_challenge_turn(record, challenge)
        updated = dict(record)
        updated["latest_challenge_id"] = challenge.challenge_id
        updated["updated_at"] = challenge.created_at
        self._write_record(resolved_bridge_id, updated)
        self._append_log_challenge(resolved_bridge_id, challenge)

        sent_turn = None
        send_error = None
        if send:
            try:
                send_result = self.send(repo_root=repo, bridge_id=resolved_bridge_id, message=repair_goal)
            except Exception as exc:  # noqa: BLE001 - preserve persisted challenge for Codex recovery.
                send_error = str(exc)
                self._append_log_status(resolved_bridge_id, f"Challenge repair send failed: {send_error}")
            else:
                updated = send_result["bridge"]
                sent_turn = send_result["latest_turn"]

        return {
            "bridge": updated,
            "challenge": challenge.to_dict(),
            "latest_turn": sent_turn,
            "send_error": send_error,
        }

    def accept(self, *, repo_root: Path, bridge_id: str | None, summary: str) -> dict[str, Any]:
        return self._finalize_supervised_bridge(
            repo_root=repo_root,
            bridge_id=bridge_id,
            status=SessionStatus.ACCEPTED,
            bridge_status="accepted",
            summary=summary,
        )

    def needs_human(self, *, repo_root: Path, bridge_id: str | None, summary: str) -> dict[str, Any]:
        return self._finalize_supervised_bridge(
            repo_root=repo_root,
            bridge_id=bridge_id,
            status=SessionStatus.NEEDS_HUMAN,
            bridge_status="needs_human",
            summary=summary,
        )

    def list(self, *, repo_root: Path) -> list[dict[str, Any]]:
        self._resolve_repo(repo_root)
        bridges = []
        for path in self._iter_bridge_dirs():
            record = self._read_record(path.name)
            bridges.append(
                {
                    "bridge_id": record["bridge_id"],
                    "repo": record["repo"],
                    "goal": record["goal"],
                    "workspace_mode": record["workspace_mode"],
                    "status": record["status"],
                    "claude_session_id": record.get("claude_session_id"),
                    "turn_count": record["turn_count"],
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                }
            )
        return sorted(bridges, key=lambda item: item["updated_at"], reverse=True)

    def _run_turn(
        self,
        *,
        repo: Path,
        bridge_id: str,
        turn_kind: str,
        message: str,
        workspace_mode: str,
        resume_session_id: str | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        command = self._build_command(
            message=message,
            workspace_mode=workspace_mode,
            resume_session_id=resume_session_id,
        )
        self._append_log_user_message(bridge_id, turn_kind, message)
        if dry_run:
            completed = CompletedProcess(command, 0, stdout="", stderr="")
        else:
            completed = self._runner(
                command,
                cwd=str(repo),
                text=True,
                capture_output=True,
                check=False,
            )

        parsed = self._parse_stdout(completed.stdout or "")
        turn = {
            "turn_id": self._turn_id_factory(),
            "kind": turn_kind,
            "message": message,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "result_text": parsed["result_text"],
            "claude_session_id": parsed["session_id"],
            "parse_error": parsed["parse_error"],
            "created_at": utc_now(),
        }
        self._append_turn(bridge_id, turn)
        self._append_log_turn_result(bridge_id, turn)
        return turn

    def _build_command(
        self,
        *,
        message: str,
        workspace_mode: str,
        resume_session_id: str | None,
    ) -> list[str]:
        command = [
            "claude",
            "--print",
            message,
            "--output-format",
            "json",
            "--permission-mode",
            "auto",
        ]
        if resume_session_id:
            command.extend(["--resume", resume_session_id])
        allowed_tools = self._allowed_tools(workspace_mode)
        if allowed_tools:
            command.extend(["--allowedTools", ",".join(allowed_tools)])
        return command

    def _allowed_tools(self, workspace_mode: str) -> list[str]:
        if workspace_mode == "readonly":
            return ["Read", "Glob", "Grep", "LS"]
        return []

    def _render_start_prompt(self, repo: Path, goal: str, workspace_mode: str) -> str:
        lines = [
            "You are Claude Code being controlled by Codex through a long-dialogue bridge.",
            f"Repository: {repo}",
            f"Goal: {goal}",
            f"Workspace mode: {workspace_mode}",
        ]
        if workspace_mode == "readonly":
            lines.append("Do not modify files. Use read-only inspection tools and report findings.")
        else:
            lines.append("Preserve unrelated user work and summarize every file you change.")
        lines.extend(
            [
                "",
                "After each turn, answer with:",
                "- what you did",
                "- important findings or changes",
                "- verification performed",
                "- what you need next from Codex or the user",
            ]
        )
        return "\n".join(lines) + "\n"

    def _parse_stdout(self, stdout: str) -> dict[str, str | None]:
        if not stdout.strip():
            return {"session_id": None, "result_text": "", "parse_error": None}
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return {
                "session_id": None,
                "result_text": stdout.strip(),
                "parse_error": str(exc),
            }
        if not isinstance(payload, dict):
            return {
                "session_id": None,
                "result_text": stdout.strip(),
                "parse_error": "Claude output was not a JSON object",
            }
        result = payload.get("result", "")
        if isinstance(result, str):
            result_text = result
        elif result is None:
            result_text = ""
        else:
            result_text = json.dumps(result, ensure_ascii=False)
        session_id = payload.get("session_id")
        return {
            "session_id": session_id if isinstance(session_id, str) else None,
            "result_text": result_text,
            "parse_error": None,
        }

    def _advance_record(self, record: dict[str, Any], turn: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        updated = dict(record)
        if turn.get("claude_session_id"):
            updated["claude_session_id"] = turn["claude_session_id"]
        if not dry_run:
            updated["status"] = "active" if turn["returncode"] == 0 else "failed"
        updated["turn_count"] = int(updated["turn_count"]) + 1
        updated["updated_at"] = turn["created_at"]
        if updated.get("supervised"):
            updated["latest_turn_id"] = turn["turn_id"]
        return updated

    def _create_supervised_session(self, repo: Path, goal: str, workspace_mode: str) -> SessionRecord:
        session = SessionRecord(
            session_id=self._session_id_factory(),
            root_task_id=self._task_id_factory(),
            repo=str(repo),
            goal=goal,
            assigned_agent="claude",
            workspace_mode=WorkspaceMode(workspace_mode),
            max_rounds=1,
        )
        self._session_recorder.start_session(session)
        return session

    def _mirror_bridge_turn(self, record: dict[str, Any], turn: dict[str, Any]) -> None:
        session_id = str(record["session_id"])
        turn_id = str(turn["turn_id"])
        task_id = str(record["root_task_id"])
        stdout_artifact = self._session_recorder.write_text_artifact(
            session_id,
            f"bridge/{turn_id}/stdout.txt",
            str(turn.get("stdout") or ""),
        )
        stderr_artifact = self._session_recorder.write_text_artifact(
            session_id,
            f"bridge/{turn_id}/stderr.txt",
            str(turn.get("stderr") or ""),
        )
        evaluation = self._evaluate_bridge_turn(turn)
        bridge_turn = TurnRecord(
            turn_id=turn_id,
            session_id=session_id,
            round_index=1,
            phase=TurnPhase.EXECUTE,
            task_id=task_id,
            run_id=turn_id,
            from_agent="claude",
            to_agent="codex",
            message=str(turn.get("message") or ""),
            decision=evaluation.next_action.value,
            summary=evaluation.summary,
            payload={"bridge_turn": turn, "evaluation": evaluation.to_dict()},
            created_at=str(turn["created_at"]),
        )
        self._session_recorder.append_turn(session_id, bridge_turn)
        trace = OutputTrace(
            trace_id=self._trace_id_factory(),
            session_id=session_id,
            turn_id=turn_id,
            run_id=turn_id,
            task_id=task_id,
            output_summary=evaluation.summary,
            agent="claude",
            adapter="ClaudeBridge",
            command=list(turn.get("command") or []),
            stdout_artifact=str(stdout_artifact),
            stderr_artifact=str(stderr_artifact),
            display_summary=str(turn.get("result_text") or evaluation.summary),
            artifact_paths=[str(stdout_artifact), str(stderr_artifact)],
            evaluation=evaluation,
            created_at=str(turn["created_at"]),
        )
        self._session_recorder.append_output_trace(session_id, trace)

    def _evaluate_bridge_turn(self, turn: dict[str, Any]) -> EvaluationOutcome:
        result_text = str(turn.get("result_text") or "")
        parse_error = turn.get("parse_error")
        structured_output = None
        if turn["returncode"] == 0 and not parse_error and result_text.strip():
            structured_output = {
                "summary": result_text,
                "status": "completed",
                "completed": True,
            }
        return self._result_evaluator.evaluate(
            WorkerResult(
                raw_output=str(turn.get("stdout") or ""),
                stdout=str(turn.get("stdout") or ""),
                stderr=str(turn.get("stderr") or ""),
                exit_code=int(turn["returncode"]),
                structured_output=structured_output,
                parse_error=str(parse_error) if parse_error else None,
            )
        )

    def _suggest_next(
        self,
        record: dict[str, Any],
        latest_verification: dict[str, Any] | None,
        latest_challenge: dict[str, Any] | None,
    ) -> dict[str, bool]:
        verification_failed = bool(latest_verification and not latest_verification.get("passed"))
        is_terminal = record.get("status") in ("accepted", "needs_human")
        challenge_pending = bool(
            not is_terminal
            and latest_challenge
            and record.get("latest_challenge_id") == latest_challenge.get("challenge_id")
        )
        return {
            "needs_codex_review": record.get("status") in ("active", "failed", "needs_human"),
            "verification_failed": verification_failed,
            "challenge_pending": challenge_pending,
        }

    def _require_supervised(self, record: dict[str, Any]) -> None:
        if not record.get("supervised") or not record.get("session_id"):
            raise ValueError(f"bridge {record['bridge_id']} is not supervised")

    def _require_not_finalized(self, record: dict[str, Any]) -> None:
        status = str(record.get("status") or "")
        if status in _TERMINAL_BRIDGE_STATUSES:
            raise ValueError(f"bridge {record['bridge_id']} is already finalized as {status}")
        if record.get("supervised") and record.get("session_id"):
            session = self._session_recorder.read_session(str(record["session_id"]))["session"]
            session_status = str(session.get("status") or "")
            if session_status in _TERMINAL_SESSION_STATUSES:
                raise ValueError(
                    f"session {record['session_id']} is already finalized as {session_status}"
                )

    def _require_bridge_turn(self, bridge_id: str, turn_id: str) -> None:
        if not any(turn.get("turn_id") == turn_id for turn in self._read_turns(bridge_id)):
            raise ValueError(f"bridge {bridge_id} has unknown bridge turn: {turn_id}")

    def _append_verification_turn(self, record: dict[str, Any], verification: VerificationRecord) -> None:
        turn = TurnRecord(
            turn_id=f"turn-{verification.verification_id}",
            session_id=str(record["session_id"]),
            round_index=1,
            phase=TurnPhase.FINAL_VERIFY,
            task_id=str(record["root_task_id"]),
            from_agent="codex",
            to_agent="codex",
            message=verification.command or "",
            decision="passed" if verification.passed else "failed",
            summary=verification.summary,
            payload={"verification": verification.to_dict()},
        )
        self._session_recorder.append_turn(str(record["session_id"]), turn)

    def _append_challenge_turn(self, record: dict[str, Any], challenge: ChallengeRecord) -> None:
        turn = TurnRecord(
            turn_id=f"turn-{challenge.challenge_id}",
            session_id=str(record["session_id"]),
            round_index=1,
            phase=TurnPhase.CHALLENGE,
            task_id=str(record["root_task_id"]),
            from_agent="codex",
            to_agent="claude",
            message=challenge.repair_goal,
            decision="challenge",
            summary=challenge.summary,
            payload={"challenge": challenge.to_dict()},
        )
        self._session_recorder.append_turn(str(record["session_id"]), turn)

    def _finalize_supervised_bridge(
        self,
        *,
        repo_root: Path,
        bridge_id: str | None,
        status: SessionStatus,
        bridge_status: str,
        summary: str,
    ) -> dict[str, Any]:
        self._resolve_repo(repo_root)
        resolved_bridge_id = self._resolve_bridge_id(bridge_id)
        record = self._read_record(resolved_bridge_id)
        self._require_supervised(record)
        session_payload = self._session_recorder.read_session(str(record["session_id"]))
        session = session_payload["session"]
        current_status = str(record.get("status") or "")
        current_session_status = str(session.get("status") or "")
        if current_status in _TERMINAL_BRIDGE_STATUSES:
            if current_status == bridge_status and current_session_status == status.value:
                return {"bridge": record, "session": session}
            if current_status == bridge_status and current_session_status not in _TERMINAL_SESSION_STATUSES:
                self._session_recorder.finalize_session(
                    str(record["session_id"]),
                    status,
                    summary,
                    current_round=1,
                )
                return {
                    "bridge": record,
                    "session": self._session_recorder.read_session(str(record["session_id"]))["session"],
                }
            raise ValueError(
                f"bridge {resolved_bridge_id} is already finalized as {current_status}; "
                f"cannot finalize as {bridge_status}"
            )
        if current_session_status in _TERMINAL_SESSION_STATUSES:
            if current_session_status != status.value:
                raise ValueError(
                    f"session {record['session_id']} is already finalized as {current_session_status}; "
                    f"cannot finalize as {status.value}"
                )
            updated = dict(record)
            updated["status"] = bridge_status
            updated["updated_at"] = utc_now()
            self._write_record(resolved_bridge_id, updated)
            self._append_log_status(resolved_bridge_id, f"{bridge_status}: {summary}")
            return {"bridge": updated, "session": session}
        self._session_recorder.finalize_session(
            str(record["session_id"]),
            status,
            summary,
            current_round=1,
        )
        updated = dict(record)
        updated["status"] = bridge_status
        updated["updated_at"] = utc_now()
        self._write_record(resolved_bridge_id, updated)
        self._append_log_status(resolved_bridge_id, f"{bridge_status}: {summary}")
        return {
            "bridge": updated,
            "session": self._session_recorder.read_session(str(record["session_id"]))["session"],
        }

    def _mark_record_running(self, record: dict[str, Any]) -> dict[str, Any]:
        updated = dict(record)
        updated["status"] = "running"
        updated["updated_at"] = utc_now()
        return updated

    def _start_visual(self, *, bridge_id: str, mode: str, dry_run: bool) -> dict[str, Any]:
        if mode == "none":
            return {"mode": "none", "launched": False}
        if mode not in ("log", "terminal"):
            raise ValueError(f"unsupported visual mode: {mode}")

        watch_script_path = self._write_watch_script(bridge_id)
        log_path = self._log_path(bridge_id)
        open_command = self._terminal_open_command(watch_script_path)
        if not dry_run:
            result = self._visual_runner(open_command, text=True, capture_output=True, check=False)
            if result.returncode != 0:
                raise CalledProcessError(
                    result.returncode,
                    result.args,
                    output=result.stdout,
                    stderr=result.stderr,
                )
        return {
            "mode": mode,
            "launched": not dry_run,
            "watch_script_path": str(watch_script_path),
            "log_path": str(log_path),
            "open_command": open_command,
        }

    def _write_watch_script(self, bridge_id: str) -> Path:
        bridge_dir = self._bridge_dir(bridge_id)
        watch_script_path = bridge_dir / "watch.zsh"
        log_path = self._log_path(bridge_id)
        script = "\n".join(
            [
                "#!/bin/zsh",
                "set -e",
                f"BRIDGE_ID={shlex.quote(bridge_id)}",
                f"LOG_PATH={shlex.quote(str(log_path))}",
                "touch \"$LOG_PATH\"",
                "printf '[orchestrator] Claude bridge log: %s\\n' \"$BRIDGE_ID\"",
                "printf '[orchestrator] Codex owns the conversation. Close this watcher with Ctrl-C.\\n\\n'",
                "tail -n +1 -f \"$LOG_PATH\"",
                "",
            ]
        )
        self._write_text(watch_script_path, script)
        watch_script_path.chmod(0o700)
        return watch_script_path

    def _terminal_open_command(self, script_path: Path) -> list[str]:
        shell_command = shlex.join(["/bin/zsh", str(script_path)])
        return [
            "osascript",
            "-e",
            'tell application "Terminal"',
            "-e",
            "activate",
            "-e",
            f"do script {json.dumps(shell_command, ensure_ascii=False)}",
            "-e",
            "end tell",
        ]

    def _log_path(self, bridge_id: str) -> Path:
        return self._bridge_dir(bridge_id) / "bridge.log"

    def _initialize_log(self, bridge_id: str, record: dict[str, Any]) -> None:
        header = "\n".join(
            [
                f"# Claude bridge log: {bridge_id}",
                f"repo: {record['repo']}",
                f"goal: {record['goal']}",
                f"workspace_mode: {record['workspace_mode']}",
                f"created_at: {record['created_at']}",
                "",
            ]
        )
        self._write_text(self._log_path(bridge_id), header)

    def _append_log_status(self, bridge_id: str, status: str) -> None:
        self._append_log_text(bridge_id, f"\n[{utc_now()}] [STATUS]\n{status}\n")

    def _append_log_user_message(self, bridge_id: str, turn_kind: str, message: str) -> None:
        self._append_log_text(
            bridge_id,
            "\n".join(
                [
                    "",
                    "=" * 72,
                    f"[{utc_now()}] [USER] {turn_kind}",
                    message.rstrip(),
                    "",
                ]
            ),
        )

    def _append_log_turn_result(self, bridge_id: str, turn: dict[str, Any]) -> None:
        parts = [
            f"[{turn['created_at']}] [CLAUDE] rc={turn['returncode']}",
            (turn.get("result_text") or "").rstrip() or "(no Claude output)",
        ]
        if turn.get("parse_error"):
            parts.extend(["", f"[PARSE_ERROR] {turn['parse_error']}"])
        if turn.get("stderr"):
            parts.extend(["", "[STDERR]", str(turn["stderr"]).rstrip()])
        self._append_log_text(bridge_id, "\n".join(parts) + "\n")

    def _append_log_verification(self, bridge_id: str, verification: VerificationRecord) -> None:
        status = "PASS" if verification.passed else "FAIL"
        self._append_log_text(
            bridge_id,
            "\n".join(
                [
                    "",
                    f"[{verification.created_at}] [VERIFY] {status}",
                    verification.command or "",
                    verification.summary,
                    "",
                ]
            ),
        )

    def _append_log_challenge(self, bridge_id: str, challenge: ChallengeRecord) -> None:
        self._append_log_text(
            bridge_id,
            "\n".join(
                [
                    "",
                    f"[{challenge.created_at}] [CHALLENGE]",
                    challenge.summary,
                    "",
                    "[REPAIR_GOAL]",
                    challenge.repair_goal,
                    "",
                ]
            ),
        )

    def _append_log_text(self, bridge_id: str, content: str) -> None:
        log_path = self._log_path(bridge_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(content)

    def _resolve_repo(self, repo_root: Path) -> Path:
        repo = repo_root.resolve()
        if not repo.is_dir():
            raise FileNotFoundError(f"repo not found: {repo}")
        return repo

    def _resolve_bridge_id(self, bridge_id: str | None) -> str:
        if bridge_id:
            return bridge_id
        latest_path = self._bridges_root / "latest"
        if not latest_path.exists():
            raise FileNotFoundError("latest Claude bridge not found")
        return latest_path.read_text(encoding="utf-8").strip()

    def _write_latest(self, bridge_id: str) -> None:
        self._write_text(self._bridges_root / "latest", bridge_id)

    def _iter_bridge_dirs(self) -> list[Path]:
        if not self._bridges_root.exists():
            return []
        return [path for path in self._bridges_root.iterdir() if path.is_dir()]

    def _bridge_dir(self, bridge_id: str) -> Path:
        return self._bridges_root / bridge_id

    def _read_record(self, bridge_id: str) -> dict[str, Any]:
        record_path = self._bridge_dir(bridge_id) / "record.json"
        if not record_path.exists():
            raise FileNotFoundError(f"Claude bridge not found: {bridge_id}")
        return json.loads(record_path.read_text(encoding="utf-8"))

    def _write_record(self, bridge_id: str, record: dict[str, Any]) -> None:
        self._write_json(self._bridge_dir(bridge_id) / "record.json", record)

    def _append_turn(self, bridge_id: str, turn: dict[str, Any]) -> None:
        turns_path = self._bridge_dir(bridge_id) / "turns.jsonl"
        turns_path.parent.mkdir(parents=True, exist_ok=True)
        with turns_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(turn, ensure_ascii=False) + "\n")

    def _read_turns(self, bridge_id: str) -> list[dict[str, Any]]:
        turns_path = self._bridge_dir(bridge_id) / "turns.jsonl"
        if not turns_path.exists():
            return []
        return [json.loads(line) for line in turns_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
