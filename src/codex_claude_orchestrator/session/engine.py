from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from codex_claude_orchestrator.core.models import (
    ChallengeRecord,
    ChallengeType,
    EvaluationOutcome,
    FailureClass,
    LearningNote,
    NextAction,
    OutputTrace,
    SessionRecord,
    SessionStatus,
    TaskRecord,
    TurnPhase,
    TurnRecord,
    WorkspaceMode,
)


class SessionEngine:
    def __init__(
        self,
        *,
        supervisor,
        run_recorder,
        session_recorder,
        verification_runner,
        skill_evolution,
    ):
        self._supervisor = supervisor
        self._run_recorder = run_recorder
        self._session_recorder = session_recorder
        self._verification_runner = verification_runner
        self._skill_evolution = skill_evolution

    def start(
        self,
        *,
        repo_root: Path,
        goal: str,
        assigned_agent: str,
        workspace_mode: WorkspaceMode | str,
        allowed_tools: list[str] | None = None,
        max_rounds: int = 1,
        verification_commands: list[str] | None = None,
        shared_write_allowed: bool = False,
    ) -> SessionRecord:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")

        repo_root = Path(repo_root)
        workspace_mode_value = WorkspaceMode(workspace_mode)
        verification_commands = list(verification_commands or [])
        root_task_id = self._new_id("task")
        session = SessionRecord(
            session_id=self._new_id("session"),
            root_task_id=root_task_id,
            repo=str(repo_root),
            goal=goal,
            assigned_agent=assigned_agent,
            workspace_mode=workspace_mode_value,
            max_rounds=max_rounds,
            verification_commands=verification_commands,
        )
        self._session_recorder.start_session(session)

        challenges: list[ChallengeRecord] = []
        current_goal = goal
        final_status = SessionStatus.NEEDS_HUMAN
        final_summary = ""

        for round_index in range(1, max_rounds + 1):
            session.current_round = round_index
            task = self._build_task(
                root_task_id=root_task_id,
                round_index=round_index,
                repo_root=repo_root,
                goal=current_goal,
                assigned_agent=assigned_agent,
                workspace_mode=workspace_mode_value,
                allowed_tools=allowed_tools or [],
                verification_commands=verification_commands,
                shared_write_allowed=shared_write_allowed,
            )
            report = self._supervisor.dispatch_with_report(task, repo_root)
            turn = TurnRecord(
                turn_id=self._new_id("turn"),
                session_id=session.session_id,
                round_index=round_index,
                phase=TurnPhase.EXECUTE,
                task_id=task.task_id,
                run_id=report.run_id,
                from_agent="codex",
                to_agent=assigned_agent,
                message=task.goal,
                decision=report.evaluation.next_action.value,
                summary=report.evaluation.summary,
                payload={"task": task.to_dict(), "evaluation": report.evaluation.to_dict()},
            )
            self._session_recorder.append_turn(session.session_id, turn)
            run_details = self._run_recorder.read_run(report.run_id)
            trace = self._build_output_trace(
                session_id=session.session_id,
                turn_id=turn.turn_id,
                task_id=task.task_id,
                run_id=report.run_id,
                run_details=run_details,
                fallback_evaluation=report.evaluation,
            )
            self._session_recorder.append_output_trace(session.session_id, trace)

            evaluation = trace.evaluation or report.evaluation
            rounds_remain = round_index < max_rounds
            if evaluation.accepted:
                failed_verification = self._run_final_verification(
                    session_id=session.session_id,
                    turn_id=turn.turn_id,
                    verification_commands=verification_commands,
                )
                if failed_verification is None:
                    final_status = SessionStatus.ACCEPTED
                    final_summary = "verification passed" if verification_commands else evaluation.summary
                    break
                if rounds_remain:
                    challenge = self._append_verification_challenge(
                        session_id=session.session_id,
                        turn_id=turn.turn_id,
                        round_index=round_index,
                        command=failed_verification.command or "",
                        summary=failed_verification.summary,
                    )
                    challenges.append(challenge)
                    current_goal = challenge.repair_goal
                    continue
                final_status = SessionStatus.NEEDS_HUMAN
                final_summary = f"Final verification failed: {failed_verification.command or failed_verification.summary}"
                break

            if rounds_remain:
                challenge = self._append_dispatch_challenge(
                    session_id=session.session_id,
                    turn_id=turn.turn_id,
                    round_index=round_index,
                    evaluation=evaluation,
                )
                challenges.append(challenge)
                current_goal = challenge.repair_goal
                continue

            final_status = SessionStatus.NEEDS_HUMAN
            final_summary = evaluation.summary
            break

        self._session_recorder.finalize_session(
            session.session_id,
            final_status,
            final_summary,
            current_round=session.current_round,
        )
        session.status = final_status
        session.final_summary = final_summary
        if challenges:
            self._record_learning(session, challenges)
        return session

    def _build_task(
        self,
        *,
        root_task_id: str,
        round_index: int,
        repo_root: Path,
        goal: str,
        assigned_agent: str,
        workspace_mode: WorkspaceMode,
        allowed_tools: list[str],
        verification_commands: list[str],
        shared_write_allowed: bool,
    ) -> TaskRecord:
        return TaskRecord(
            task_id=root_task_id if round_index == 1 else self._new_id("task"),
            parent_task_id=None if round_index == 1 else root_task_id,
            origin="session",
            assigned_agent=assigned_agent,
            goal=goal,
            task_type="adversarial_session",
            scope=str(repo_root),
            workspace_mode=workspace_mode,
            allowed_tools=list(allowed_tools),
            verification_expectations=list(verification_commands),
            shared_write_allowed=shared_write_allowed,
        )

    def _build_output_trace(
        self,
        *,
        session_id: str,
        turn_id: str,
        task_id: str,
        run_id: str,
        run_details: dict[str, Any],
        fallback_evaluation: EvaluationOutcome,
    ) -> OutputTrace:
        run = run_details.get("run") or {}
        result = run_details.get("result") or {}
        artifacts = list(run_details.get("artifacts") or [])
        evaluation = self._evaluation_from_details(run_details.get("evaluation"), fallback_evaluation)
        command = self._command_from_run(run)
        summary = evaluation.summary or run.get("result_summary", "")
        return OutputTrace(
            trace_id=self._new_id("trace"),
            session_id=session_id,
            turn_id=turn_id,
            run_id=run_id,
            task_id=task_id,
            output_summary=summary,
            agent=run.get("agent", ""),
            adapter=run.get("adapter", ""),
            prompt_artifact=self._find_artifact(artifacts, "prompt.txt"),
            command=command,
            stdout_artifact=self._find_artifact(artifacts, "stdout.txt"),
            stderr_artifact=self._find_artifact(artifacts, "stderr.txt"),
            structured_output_artifact=self._find_artifact(artifacts, "structured_output.json"),
            display_summary=summary,
            artifact_paths=artifacts,
            changed_files=list(result.get("changed_files") or []),
            evaluation=evaluation,
        )

    def _run_final_verification(
        self,
        *,
        session_id: str,
        turn_id: str,
        verification_commands: list[str],
    ):
        for command in verification_commands:
            record = self._verification_runner.run(session_id, turn_id, command)
            if not record.passed:
                return record
        return None

    def _append_dispatch_challenge(
        self,
        *,
        session_id: str,
        turn_id: str,
        round_index: int,
        evaluation: EvaluationOutcome,
    ) -> ChallengeRecord:
        summary = f"Dispatch was not accepted: {evaluation.summary}"
        repair_goal = "\n".join(
            [
                "Repair the previous attempt so it satisfies the original goal.",
                f"Previous evaluation: {evaluation.summary}",
                "Address the failure, preserve unrelated work, and provide evidence in the result.",
            ]
        )
        challenge = ChallengeRecord(
            challenge_id=self._new_id("challenge"),
            session_id=session_id,
            turn_id=turn_id,
            round_index=round_index,
            challenge_type=ChallengeType.QUALITY_RISK,
            summary=summary,
            question="What change is needed for the worker result to be accepted?",
            expected_evidence="A corrected implementation and evidence that the original issue is resolved.",
            severity=2,
            evidence={"evaluation": evaluation.to_dict()},
            repair_goal=repair_goal,
        )
        self._session_recorder.append_challenge(session_id, challenge)
        return challenge

    def _append_verification_challenge(
        self,
        *,
        session_id: str,
        turn_id: str,
        round_index: int,
        command: str,
        summary: str,
    ) -> ChallengeRecord:
        challenge_summary = f"Final verification failed: {command or summary}"
        repair_goal = "\n".join(
            [
                "Verification failed after an accepted dispatch.",
                f"Failing command: {command or 'unknown'}",
                f"Verification summary: {summary}",
                "Repair the implementation and keep the original goal satisfied.",
            ]
        )
        challenge = ChallengeRecord(
            challenge_id=self._new_id("challenge"),
            session_id=session_id,
            turn_id=turn_id,
            round_index=round_index,
            challenge_type=ChallengeType.MISSING_TEST,
            summary=challenge_summary,
            question="What repair makes the final verification pass?",
            expected_evidence="The failing verification command passes after the repair.",
            severity=3,
            evidence={"command": command, "summary": summary},
            repair_goal=repair_goal,
        )
        self._session_recorder.append_challenge(session_id, challenge)
        return challenge

    def _record_learning(self, session: SessionRecord, challenges: list[ChallengeRecord]) -> None:
        summaries = [challenge.summary for challenge in challenges]
        learning_note = LearningNote(
            note_id=self._new_id("learning"),
            session_id=session.session_id,
            challenge_ids=[challenge.challenge_id for challenge in challenges],
            summary="Session repairs should address evaluator and verification feedback before completion.",
            proposed_skill_name=f"session-repair-{session.session_id}",
            source_turn_ids=[challenge.turn_id for challenge in challenges],
            pattern="; ".join(summaries),
            trigger_conditions=["adversarial session retry", "failed final verification"],
            evidence_summary="; ".join(summaries),
            confidence=0.6,
        )
        self._session_recorder.append_learning_note(session.session_id, learning_note)
        self._skill_evolution.create_pending_skill(learning_note)

    def _evaluation_from_details(
        self,
        payload: dict[str, Any] | None,
        fallback: EvaluationOutcome,
    ) -> EvaluationOutcome:
        if not payload:
            return fallback
        failure_class = payload.get("failure_class")
        return EvaluationOutcome(
            accepted=bool(payload.get("accepted")),
            next_action=NextAction(payload.get("next_action", fallback.next_action.value)),
            summary=payload.get("summary", fallback.summary),
            failure_class=FailureClass(failure_class) if failure_class else None,
            needs_human=bool(payload.get("needs_human", False)),
        )

    def _command_from_run(self, run: dict[str, Any]) -> list[str]:
        invocation = run.get("adapter_invocation") or {}
        command = invocation.get("command") or []
        if isinstance(command, str):
            return [command]
        return [str(part) for part in command]

    def _find_artifact(self, artifacts: list[str], name: str) -> str | None:
        for artifact in artifacts:
            if artifact == name or artifact.endswith(f"/{name}"):
                return artifact
        return None

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid4().hex}"
