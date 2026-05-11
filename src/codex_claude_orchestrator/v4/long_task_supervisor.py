"""LongTaskSupervisor: orchestrates multi-stage adversarial execution.

Main loop: for each stage -> build briefing -> spawn workers -> merge ->
reviewer -> challenge/refine -> record result -> plan next stage.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.v4.long_task_models import (
    Briefing,
    ChallengeTarget,
    Contract,
    PlanAdversaryVerdict,
    ProjectContext,
    ReviewVerdict,
    StagePlan,
    SubTaskRef,
    ThinkResult,
)

class LongTaskSupervisor:
    """Orchestrates long task execution with multi-stage adversarial verification.

    Usage::

        supervisor = LongTaskSupervisor(
            controller=controller,
            supervisor=supervisor_adapter,
            event_store=event_store,
            repo_root=Path("/path/to/repo"),
            goal="refactor auth module",
            verification_commands=["pytest"],
        )
        supervisor.supervise_long_task()
    """

    def __init__(
        self,
        *,
        controller: Any,
        supervisor: Any,
        event_store: Any,
        repo_root: Path,
        goal: str,
        verification_commands: list[str],
        max_rounds: int = 3,
        prompt_dir: Path | None = None,
        crew_id: str = "",
    ) -> None:
        self.controller = controller
        self.supervisor = supervisor
        self.event_store = event_store
        self.repo_root = repo_root
        self.goal = goal
        self.verification_commands = verification_commands
        self.max_rounds = max_rounds
        self.prompt_dir = prompt_dir or Path(".claude/prompts")
        self._crew_id: str = crew_id

    # ------------------------------------------------------------------
    # ThinkResult validation
    # ------------------------------------------------------------------

    def load_and_validate_think_result(self, path: Path) -> ThinkResult:
        """Load and validate think_result.json.

        Raises ValueError if file missing, fields missing, or stages invalid.
        """
        if not path.exists():
            raise ValueError(f"think_result.json not found at {path}")

        data = json.loads(path.read_text())

        required = ["spec", "stages", "contract", "project_context", "acceptance_criteria"]
        missing = [f for f in required if f not in data]
        if missing:
            raise ValueError(f"think_result.json missing fields: {missing}")

        if not data["stages"]:
            raise ValueError("think_result.json has no stages")

        for i, stage in enumerate(data["stages"]):
            if "goal" not in stage:
                raise ValueError(f"stages[{i}] missing 'goal'")
            if "sub_tasks" not in stage or not stage["sub_tasks"]:
                raise ValueError(f"stages[{i}] has no sub_tasks")

        return ThinkResult.from_dict(data)

    # ------------------------------------------------------------------
    # Briefing
    # ------------------------------------------------------------------

    def build_briefing(
        self,
        stage: StagePlan,
        completed_stages: list[dict[str, Any]],
        think_result: ThinkResult,
    ) -> Briefing:
        """Build context briefing for a stage."""
        return Briefing(
            overall_goal=think_result.spec,
            current_stage=stage,
            contract=stage.contract,
            previous_summaries=[s["summary"] for s in completed_stages],
            key_decisions=self._extract_decisions(completed_stages),
            constraints=think_result.project_context.constraints,
            pending_questions=think_result.open_questions,
            verification_commands=self.verification_commands,
        )

    def _extract_decisions(self, completed_stages: list[dict[str, Any]]) -> list[str]:
        """Extract key decisions from completed stages."""
        decisions: list[str] = []
        for stage_data in completed_stages:
            if "decisions" in stage_data:
                decisions.extend(stage_data["decisions"])
        return decisions

    # ------------------------------------------------------------------
    # Reviewer
    # ------------------------------------------------------------------

    def run_reviewer(
        self,
        stage: StagePlan,
        sub_task_results: list[Any],
        briefing: Briefing,
    ) -> ReviewVerdict:
        """Read review prompt template, spawn reviewer sub-agent, parse verdict."""
        changed_files = self.collect_changed_files(sub_task_results)
        template = (self.prompt_dir / "review.md").read_text()
        prompt = template.format(
            overall_goal=briefing.overall_goal,
            stage_goal=stage.goal,
            acceptance_criteria=", ".join(stage.acceptance_criteria),
            contract=stage.contract.to_json(),
            previous_summaries="\n".join(briefing.previous_summaries) or "（无）",
            changed_files="\n".join(changed_files) or "（无）",
            verification_commands="\n".join(briefing.verification_commands),
        )
        result = self._spawn_sub_agent(prompt, tools=["Read", "Grep", "Glob", "Bash"])
        return self.parse_review_verdict(result)

    def parse_review_verdict(self, agent_output: str) -> ReviewVerdict:
        """Parse ReviewVerdict from sub-agent output.

        Tries: JSON block in markdown -> raw JSON -> raise.
        """
        # Try JSON block in markdown
        match = re.search(r"```json\s*(.*?)\s*```", agent_output, re.DOTALL)
        if match:
            return ReviewVerdict.from_json(match.group(1))

        # Try raw JSON
        stripped = agent_output.strip()
        if stripped.startswith("{"):
            return ReviewVerdict.from_json(stripped)

        raise ValueError(
            f"Failed to parse ReviewVerdict from agent output. "
            f"Expected JSON block or raw JSON. Got: {agent_output[:200]}"
        )

    # ------------------------------------------------------------------
    # Plan Adversary
    # ------------------------------------------------------------------

    def run_plan_adversary(self, think_result_path: Path) -> PlanAdversaryVerdict:
        """Read plan-adversary prompt template, spawn sub-agent, parse verdict."""
        template = (self.prompt_dir / "plan-adversary.md").read_text()
        prompt = template.format(
            think_result_json=think_result_path.read_text(),
            user_goal=self.goal,
        )
        result = self._spawn_sub_agent(prompt, tools=["Read", "Grep", "Glob"])
        return self.parse_plan_adversary_verdict(result)

    def parse_plan_adversary_verdict(self, agent_output: str) -> PlanAdversaryVerdict:
        """Parse PlanAdversaryVerdict from sub-agent output."""
        match = re.search(r"```json\s*(.*?)\s*```", agent_output, re.DOTALL)
        if match:
            return PlanAdversaryVerdict.from_json(match.group(1))

        stripped = agent_output.strip()
        if stripped.startswith("{"):
            return PlanAdversaryVerdict.from_json(stripped)

        raise ValueError(
            f"Failed to parse PlanAdversaryVerdict from agent output. "
            f"Expected JSON block or raw JSON. Got: {agent_output[:200]}"
        )

    # ------------------------------------------------------------------
    # StagePlanner
    # ------------------------------------------------------------------

    def plan_next_stage(
        self,
        stages: list[StagePlan],
        completed_stages: list[dict[str, Any]],
        think_result: ThinkResult,
    ) -> StagePlan:
        """Read plan-stage prompt template, spawn sub-agent, parse stage plan."""
        template = (self.prompt_dir / "plan-stage.md").read_text()
        prompt = template.format(
            overall_goal=think_result.spec,
            completed_stages=json.dumps(
                [s if isinstance(s, dict) else s.to_dict() for s in completed_stages],
                ensure_ascii=False, indent=2,
            ),
            project_context=json.dumps(
                think_result.project_context.to_dict(), ensure_ascii=False, indent=2,
            ),
            contract=think_result.contract.to_json(),
        )
        result = self._spawn_sub_agent(prompt, tools=["Read", "Grep", "Glob"])
        return self._parse_stage_plan(result)

    def _parse_stage_plan(self, agent_output: str) -> StagePlan:
        """Parse StagePlan from sub-agent output."""
        match = re.search(r"```json\s*(.*?)\s*```", agent_output, re.DOTALL)
        text = match.group(1) if match else agent_output.strip()

        if not text or text == "{}":
            raise ValueError("StagePlanner returned empty plan (task complete)")

        return StagePlan.from_dict(json.loads(text))

    # ------------------------------------------------------------------
    # Challenge helpers
    # ------------------------------------------------------------------

    def challenge_parallel_workers(self, challenge_targets: list[ChallengeTarget]) -> None:
        """Send challenge messages to workers via their tmux sessions."""
        for target in challenge_targets:
            message = self.build_challenge_message(target)
            # Deliver challenge to the worker's existing tmux session
            self.supervisor.send_worker(
                repo_root=self.repo_root,
                crew_id=self._crew_id,
                worker_id=target.worker_id,
                message=message,
            )

    def build_challenge_message(self, target: ChallengeTarget) -> str:
        """Build challenge prompt with affected_files hint."""
        files_hint = ", ".join(target.affected_files) if target.affected_files else "未指定"
        return f"""Reviewer 发现以下问题需要修复：

**问题：** {target.challenge_message}
**影响文件：** {files_hint}

请修复上述问题。完成后输出结构化摘要。"""

    def collect_changed_files(self, sub_task_results: list[Any]) -> list[str]:
        """Collect changed files from all worker results, deduplicated."""
        files: list[str] = []
        for result in sub_task_results:
            if hasattr(result, "changed_files"):
                files.extend(result.changed_files)
        return list(set(files))

    def replan_remaining_stages(
        self,
        current_stage: StagePlan,
        completed_stages: list[dict[str, Any]],
        think_result: ThinkResult,
        reason: str,
    ) -> StagePlan:
        """Reviewer determined replan is needed; plan next stage."""
        return self.plan_next_stage(
            stages=[current_stage],
            completed_stages=completed_stages,
            think_result=think_result,
        )

    def should_plan_next(
        self, stages: list[StagePlan], completed_stages: list[dict[str, Any]]
    ) -> bool:
        """All known stages completed; time to plan the next one."""
        return len(completed_stages) >= len(stages)

    def get_active_turns(self, stage: StagePlan) -> dict[str, Any]:
        """Get active worker turns for a stage."""
        if hasattr(self.supervisor, "get_active_turns"):
            return self.supervisor.get_active_turns(crew_id=self._crew_id)
        return {}

    def get_updated_results(
        self, stage: StagePlan, active_turns: dict[str, Any]
    ) -> list[Any]:
        """Get updated results after challenge rounds."""
        results = []
        for worker_id, turn in active_turns.items():
            # Wait for the worker to complete the challenge turn
            for event in self.supervisor.watch_turn(turn):
                pass  # handle events
            results.append(self._read_worker_outbox(worker_id))
        return results

    def _read_worker_outbox(self, worker_id: str) -> Any:
        """Read worker's structured output from event store."""
        events = self.event_store.list_stream(worker_id)
        for event_type in ("turn.completed", "artifact.written"):
            for event in reversed(events):
                if event.get("type") == event_type:
                    return event.get("payload", {})
        raise ValueError(f"no output found for worker {worker_id}")

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_stage_results(self, stage: StagePlan, worker_results: list[Any]) -> None:
        """Merge all worker results into the main worktree.

        write_scope guarantees no file conflicts between workers.
        """
        for result in worker_results:
            if hasattr(result, "success") and not result.success:
                continue
            if isinstance(result, dict) and result.get("status") == "error":
                continue
            changed = result.get("changed_files", []) if isinstance(result, dict) else []
            worker_id = result.get("worker_id", "") if isinstance(result, dict) else ""
            if not changed:
                continue
            try:
                pool = self.controller._worker_pool
                diff = pool.worktree_manager.get_diff(
                    repo_root=self.repo_root,
                    worker_id=worker_id,
                    crew_id=self._crew_id,
                )
                if diff:
                    subprocess.run(
                        ["git", "apply", "--3way"],
                        input=diff, cwd=self.repo_root,
                        capture_output=True, text=True,
                    )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Sub-agent spawning (placeholder for Task 6)
    # ------------------------------------------------------------------

    def _spawn_sub_agent(self, prompt: str, tools: list[str] | None = None) -> str:
        """Spawn a lightweight sub-agent and return its output.

        Uses supervisor.run_worker_turn for a one-shot execution.
        """
        from codex_claude_orchestrator.crew.models import (
            AuthorityLevel,
            WorkerContract,
            WorkspacePolicy,
        )

        worker_id = f"sub-agent-{uuid.uuid4().hex[:8]}"
        round_id = f"sub-{uuid.uuid4().hex[:8]}"

        contract = WorkerContract(
            contract_id=f"contract-{worker_id}",
            label="sub-agent",
            mission=prompt[:200],
            required_capabilities=tools or [],
            authority_level=AuthorityLevel.READONLY,
            workspace_policy=WorkspacePolicy.READONLY,
        )

        self.controller.ensure_worker(
            repo_root=self.repo_root,
            crew_id=self._crew_id,
            contract=contract,
            allow_dirty_base=True,
        )

        result = self.supervisor.run_worker_turn(
            crew_id=self._crew_id,
            goal=prompt[:200],
            worker_id=worker_id,
            round_id=round_id,
            phase="sub-agent",
            contract_id=f"sub-{worker_id}",
            message=prompt,
            expected_marker="<<<DONE",
        )

        if result.get("status") == "error":
            raise RuntimeError(f"Sub-agent failed: {result.get('reason', 'unknown')}")

        return result.get("output", "")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def supervise_long_task(self, crew_id: str = "") -> dict[str, Any]:
        """Main long task supervision loop.

        Returns a summary dict with final status.
        """
        self._crew_id = crew_id or self._crew_id

        # Phase 1: Load brainstorming output
        think_result_path = self.repo_root / ".crew" / "think_result.json"
        think_result = self.load_and_validate_think_result(think_result_path)

        # Phase 2: Do (adversarial execution)
        completed_stages: list[dict[str, Any]] = []
        stages = list(think_result.stages)

        # Record stage.planned events
        for stage in stages:
            self.event_store.append(
                stream_id=self._crew_id or "crew-long-task",
                type="stage.planned",
                crew_id=self._crew_id,
                payload=stage.to_event_dict(),
            )

        for stage in stages:
            # Build briefing
            briefing = self.build_briefing(stage, completed_stages, think_result)

            # Run sub-tasks (parallel workers)
            sub_task_results = self._run_sub_tasks(stage, briefing)

            # Merge worker code before review
            self.merge_stage_results(stage, sub_task_results)

            # Reviewer
            review = self.run_reviewer(stage, sub_task_results, briefing)

            # Challenge/Refine loop
            for round_idx in range(self.max_rounds):
                if review.action == "pass":
                    break
                elif review.action == "challenge":
                    if review.challenge_targets:
                        self.challenge_parallel_workers(review.challenge_targets)
                        # TODO: get_active_turns / get_updated_results need WorkerPool integration
                        active_turns = self.get_active_turns(stage)
                        sub_task_results = self.get_updated_results(stage, active_turns)
                        review = self.run_reviewer(stage, sub_task_results, briefing)
                elif review.action == "replan":
                    remaining = self.replan_remaining_stages(
                        stage, completed_stages, think_result, review.replan_reason or ""
                    )
                    # Replace remaining stages with replanned one
                    stage_idx = stages.index(stage)
                    stages = stages[: stage_idx + 1] + [remaining]
                    break

            # Record stage result
            summary = review.stage_summary
            completed_stages.append({
                "stage_id": stage.stage_id,
                "summary": summary,
                "verdict": review.verdict,
                "action": review.action,
            })

            # Record stage.completed event
            self.event_store.append(
                stream_id=self._crew_id or "crew-long-task",
                type="stage.completed",
                crew_id=self._crew_id,
                payload={
                    "stage_id": stage.stage_id,
                    "summary": summary,
                    "verdict": review.verdict,
                    "action": review.action,
                },
            )

            # Dynamic planning: plan next stage if all known stages completed
            if self.should_plan_next(stages, completed_stages):
                try:
                    next_stage = self.plan_next_stage(
                        stages, completed_stages, think_result
                    )
                    stages.append(next_stage)
                    self.event_store.append(
                        stream_id=self._crew_id or "crew-long-task",
                        type="stage.planned",
                        crew_id=self._crew_id,
                        payload=next_stage.to_event_dict(),
                    )
                except ValueError:
                    # No more stages to plan (task complete)
                    pass

        # Phase 3: Final verification
        self._run_final_verification()
        self._accept()

        return {
            "status": "done",
            "completed_stages": completed_stages,
            "total_stages": len(completed_stages),
        }

    def _run_sub_tasks(self, stage: StagePlan, briefing: Briefing) -> list[Any]:
        """Spawn and run all sub-tasks for a stage via V4CrewRunner."""
        from codex_claude_orchestrator.v4.crew_runner import V4CrewRunner

        if not stage.sub_tasks:
            return []

        results = []

        def _run_one(sub_task: SubTaskRef) -> dict[str, Any]:
            runner = V4CrewRunner(
                controller=self.controller,
                supervisor=self.supervisor,
                event_store=self.event_store,
            )
            return runner.supervise(
                repo_root=self.repo_root,
                crew_id=self._crew_id,
                verification_commands=self.verification_commands,
                max_rounds=self.max_rounds,
            )

        with ThreadPoolExecutor(max_workers=min(len(stage.sub_tasks), 4)) as pool:
            futures = {pool.submit(_run_one, st): st for st in stage.sub_tasks}
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({"status": "error", "reason": str(exc)})

        return results

    def _run_final_verification(self) -> None:
        """Run final verification commands."""
        for command in self.verification_commands:
            result = subprocess.run(
                command, shell=True, cwd=self.repo_root,
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                self.event_store.append(
                    stream_id=self._crew_id or "crew-long-task",
                    type="verification.passed",
                    crew_id=self._crew_id,
                    payload={"command": command, "returncode": 0},
                )
            else:
                self.event_store.append(
                    stream_id=self._crew_id or "crew-long-task",
                    type="verification.failed",
                    crew_id=self._crew_id,
                    payload={
                        "command": command,
                        "returncode": result.returncode,
                        "stdout": result.stdout[-500:],
                        "stderr": result.stderr[-500:],
                    },
                )

    def _accept(self) -> None:
        """Mark task as accepted via controller."""
        self.controller.accept(
            crew_id=self._crew_id,
            summary=f"Long task completed: {self.goal}",
        )
