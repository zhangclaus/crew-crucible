from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.core.models import DispatchReport, EventRecord, RunRecord, TaskRecord, WorkerResult


class Supervisor:
    def __init__(
        self,
        *,
        prompt_compiler,
        workspace_manager,
        adapter,
        policy_gate,
        run_recorder,
        result_evaluator,
    ):
        self._prompt_compiler = prompt_compiler
        self._workspace_manager = workspace_manager
        self._adapter = adapter
        self._policy_gate = policy_gate
        self._run_recorder = run_recorder
        self._result_evaluator = result_evaluator

    def dispatch(self, task: TaskRecord, source_repo: Path):
        return self.dispatch_with_report(task, source_repo).evaluation

    def dispatch_with_report(self, task: TaskRecord, source_repo: Path) -> DispatchReport:
        compiled = self._prompt_compiler.compile(task)
        allocation = self._workspace_manager.prepare(source_repo, task)
        command = self._adapter.build_command(compiled)
        run = RunRecord(
            run_id=str(uuid4()),
            task_id=task.task_id,
            agent=task.assigned_agent,
            adapter=self._adapter.__class__.__name__,
            workspace_id=allocation.workspace_id,
            adapter_invocation={"command": command},
        )
        self._run_recorder.start_run(run, task, compiled)
        self._run_recorder.append_event(
            run.run_id,
            EventRecord(
                event_id=str(uuid4()),
                task_id=task.task_id,
                run_id=run.run_id,
                from_agent="codex",
                to_agent=task.assigned_agent,
                event_type="task_dispatched",
                payload={"goal": task.goal, "command": command},
            ),
        )

        workspace_decision = self._policy_gate.guard_workspace_execution(
            allocation,
            shared_write_allowed=task.shared_write_allowed,
        )
        if not workspace_decision.allowed:
            result = WorkerResult(
                raw_output="",
                stdout="",
                stderr="",
                exit_code=0,
            )
            evaluation = self._result_evaluator.evaluate(result, workspace_decision)
            self._run_recorder.write_result(run.run_id, result, evaluation)
            return DispatchReport(run_id=run.run_id, task_id=task.task_id, evaluation=evaluation)

        command_decision = self._policy_gate.guard_command(command)
        if not command_decision.allowed:
            result = WorkerResult(
                raw_output="",
                stdout="",
                stderr="",
                exit_code=0,
            )
            evaluation = self._result_evaluator.evaluate(result, command_decision)
            self._run_recorder.write_result(run.run_id, result, evaluation)
            return DispatchReport(run_id=run.run_id, task_id=task.task_id, evaluation=evaluation)

        result = self._adapter.execute(compiled, allocation)
        result.changed_files = self._workspace_manager.detect_changes(allocation)
        write_decision = self._policy_gate.guard_write_targets(
            allocation,
            result.changed_files,
            shared_write_allowed=task.shared_write_allowed,
        )
        evaluation = self._result_evaluator.evaluate(result, write_decision)
        self._run_recorder.write_result(run.run_id, result, evaluation)
        self._run_recorder.append_event(
            run.run_id,
            EventRecord(
                event_id=str(uuid4()),
                task_id=task.task_id,
                run_id=run.run_id,
                from_agent=task.assigned_agent,
                to_agent="codex",
                event_type="evaluation_completed",
                payload=evaluation.to_dict(),
            ),
        )
        return DispatchReport(run_id=run.run_id, task_id=task.task_id, evaluation=evaluation)
