from __future__ import annotations

from uuid import uuid4

from codex_claude_orchestrator.crew.models import (
    AuthorityLevel,
    DecisionAction,
    DecisionActionType,
    WorkerContract,
    WorkspacePolicy,
)


class CrewDecisionPolicy:
    version = "dynamic-contract-mvp-v1"

    def decide(self, snapshot: dict) -> DecisionAction:
        crew_id = snapshot.get("crew_id", "")
        workers = snapshot.get("workers", [])
        changed_files = snapshot.get("changed_files", [])
        verification_failures = snapshot.get("verification_failures", [])

        if snapshot.get("context_insufficient") and not self._has_capability(workers, "design_architecture"):
            return self._spawn(
                crew_id,
                self._context_scout_contract(crew_id, snapshot.get("goal", "")),
                "context is insufficient before source edits",
                priority=92,
            )

        if (
            len(verification_failures) >= 3
            and self._has_capability(workers, "triage_failure")
            and not self._has_capability(workers, "maintain_guardrails")
        ):
            return self._spawn(
                crew_id,
                self._guardrail_maintainer_contract(crew_id, verification_failures),
                "same verification command failed three times",
                priority=98,
            )

        if len(verification_failures) >= 2 and not self._has_capability(workers, "triage_failure"):
            return self._spawn(
                crew_id,
                self._failure_analyst_contract(crew_id, verification_failures),
                "same verification command failed repeatedly",
                priority=95,
            )

        if changed_files and not snapshot.get("review_status") and not self._has_capability(workers, "review_patch"):
            return self._spawn(
                crew_id,
                self._patch_auditor_contract(crew_id, changed_files),
                "patch exists without independent review",
                priority=80,
            )

        if (
            changed_files
            and (self._goal_needs_browser(snapshot.get("goal", "")) or "frontend" in snapshot.get("repo_risk_tags", []))
            and snapshot.get("review_status") == "ok"
            and not snapshot.get("browser_check_status")
            and not self._has_capability(workers, "browser_e2e")
        ):
            return self._spawn(
                crew_id,
                self._browser_flow_contract(crew_id, changed_files),
                "UI/browser goal needs browser flow verification",
                priority=75,
            )

        if snapshot.get("verification_passed") and (not changed_files or snapshot.get("review_status") in {None, "ok"}):
            return DecisionAction(
                action_id=self._action_id(),
                crew_id=crew_id,
                action_type=DecisionActionType.ACCEPT_READY,
                reason="verification passed and no blocking review remains",
                priority=100,
            )

        if not self._has_source_write_worker(workers):
            return self._spawn(
                crew_id,
                self._source_write_contract(
                    crew_id,
                    snapshot.get("goal", ""),
                    self._source_write_scope(snapshot),
                ),
                "no compatible source_write worker is active",
                priority=90,
            )

        return DecisionAction(
            action_id=self._action_id(),
            crew_id=crew_id,
            action_type=DecisionActionType.OBSERVE_WORKER,
            worker_id=self._source_write_worker_id(workers),
            reason="source_write worker is active",
            priority=50,
        )

    def _source_write_contract(self, crew_id: str, goal: str, write_scope: list[str]) -> WorkerContract:
        return WorkerContract(
            contract_id=f"contract-source-write-{uuid4().hex[:8]}",
            label="targeted-code-editor",
            mission=f"Implement the smallest safe patch for this goal: {goal}",
            required_capabilities=["inspect_code", "edit_source", "edit_tests", "run_verification"],
            authority_level=AuthorityLevel.SOURCE_WRITE,
            workspace_policy=WorkspacePolicy.WORKTREE,
            write_scope=write_scope,
            expected_outputs=["patch", "changed_files", "verification_notes"],
            acceptance_criteria=["all requested verification commands pass"],
            protocol_refs=["task_confirmation", "doc_code_sync"],
            completion_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=source_write>>>",
            spawn_reason="goal requires source edits",
        )

    def _context_scout_contract(self, crew_id: str, goal: str) -> WorkerContract:
        return WorkerContract(
            contract_id=f"contract-context-scout-{uuid4().hex[:8]}",
            label="repo-context-scout",
            mission=f"Map the repository context, risks, and relevant boundaries before source edits for: {goal}",
            required_capabilities=["inspect_code", "design_architecture"],
            authority_level=AuthorityLevel.READONLY,
            workspace_policy=WorkspacePolicy.READONLY,
            expected_outputs=["relevant_files", "architecture_summary", "risks", "smallest_next_step"],
            acceptance_criteria=["report facts and boundaries without editing files"],
            protocol_refs=["task_confirmation"],
            completion_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=context_scout>>>",
            spawn_reason="context is insufficient before source edits",
        )

    def _patch_auditor_contract(self, crew_id: str, changed_files: list[str]) -> WorkerContract:
        return WorkerContract(
            contract_id=f"contract-patch-auditor-{uuid4().hex[:8]}",
            label="patch-risk-auditor",
            mission="Review the current patch for correctness, regressions, and API risk.",
            required_capabilities=["review_patch", "inspect_code"],
            authority_level=AuthorityLevel.READONLY,
            workspace_policy=WorkspacePolicy.READONLY,
            context_refs=changed_files,
            expected_outputs=["verdict", "findings", "risk_summary"],
            acceptance_criteria=["report OK/WARN/BLOCK with evidence refs"],
            protocol_refs=["review_dimensions"],
            completion_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=patch_auditor>>>",
            spawn_reason="patch exists without independent review",
        )

    def _failure_analyst_contract(self, crew_id: str, failures: list[dict]) -> WorkerContract:
        return WorkerContract(
            contract_id=f"contract-failure-analyst-{uuid4().hex[:8]}",
            label="verification-failure-analyst",
            mission="Classify repeated verification failures and propose the smallest repair path.",
            required_capabilities=["triage_failure", "inspect_code"],
            authority_level=AuthorityLevel.READONLY,
            workspace_policy=WorkspacePolicy.READONLY,
            context_refs=[item.get("summary", "") for item in failures if item.get("summary")],
            expected_outputs=["failure_class", "root_cause_hypothesis", "repair_instruction"],
            acceptance_criteria=["identify whether retry, new contract, guardrail, or human help is needed"],
            protocol_refs=["three_strike_escalation", "failure_to_guardrail"],
            completion_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=failure_analyst>>>",
            spawn_reason="same verification command failed twice",
        )

    def _browser_flow_contract(self, crew_id: str, changed_files: list[str]) -> WorkerContract:
        return WorkerContract(
            contract_id=f"contract-browser-flow-{uuid4().hex[:8]}",
            label="browser-flow-tester",
            mission="Verify the changed user flow in a browser-capable environment and report visible regressions.",
            required_capabilities=["browser_e2e", "review_patch"],
            authority_level=AuthorityLevel.READONLY,
            workspace_policy=WorkspacePolicy.READONLY,
            context_refs=changed_files,
            expected_outputs=["flow_tested", "result", "reproduction_steps", "artifact_refs"],
            acceptance_criteria=["report pass/fail with enough evidence for Codex to decide"],
            protocol_refs=["review_dimensions"],
            completion_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=browser_flow>>>",
            spawn_reason="goal involves browser/UI/user flow",
        )

    def _guardrail_maintainer_contract(self, crew_id: str, failures: list[dict]) -> WorkerContract:
        return WorkerContract(
            contract_id=f"contract-guardrail-maintainer-{uuid4().hex[:8]}",
            label="guardrail-maintainer",
            mission="Turn repeated verification failures into a narrow known pitfall and guardrail recommendation.",
            required_capabilities=["maintain_guardrails", "triage_failure", "inspect_code"],
            authority_level=AuthorityLevel.READONLY,
            workspace_policy=WorkspacePolicy.READONLY,
            context_refs=[item.get("summary", "") for item in failures if item.get("summary")],
            expected_outputs=["known_pitfall", "guardrail", "evidence_refs", "proposed_check"],
            acceptance_criteria=["guardrail must be specific to the observed failure class"],
            protocol_refs=["three_strike_escalation", "failure_to_guardrail"],
            completion_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=guardrail>>>",
            spawn_reason="same verification command failed three times",
        )

    def _spawn(self, crew_id: str, contract: WorkerContract, reason: str, *, priority: int) -> DecisionAction:
        return DecisionAction(
            action_id=self._action_id(),
            crew_id=crew_id,
            action_type=DecisionActionType.SPAWN_WORKER,
            contract=contract,
            reason=reason,
            priority=priority,
        )

    def _has_source_write_worker(self, workers: list[dict]) -> bool:
        return any(
            worker.get("status") in {"running", "idle"}
            and worker.get("authority_level") == AuthorityLevel.SOURCE_WRITE.value
            for worker in workers
        )

    def _source_write_worker_id(self, workers: list[dict]) -> str | None:
        for worker in workers:
            if worker.get("authority_level") == AuthorityLevel.SOURCE_WRITE.value and worker.get("status") in {"running", "idle"}:
                return worker.get("worker_id")
        return None

    def _has_capability(self, workers: list[dict], capability: str) -> bool:
        return any(worker.get("status") in {"running", "idle"} and capability in worker.get("capabilities", []) for worker in workers)

    def _goal_needs_browser(self, goal: str) -> bool:
        normalized = goal.lower()
        keywords = ("browser", "ui", "frontend", "e2e", "playwright", "user flow", "页面", "前端", "浏览器", "用户流")
        return any(keyword in normalized for keyword in keywords)

    def _source_write_scope(self, snapshot: dict) -> list[str]:
        scope = snapshot.get("repo_write_scope") or snapshot.get("write_scope") or []
        normalized = [item for item in scope if isinstance(item, str) and item]
        return list(dict.fromkeys(normalized)) or ["src/", "tests/"]

    def _action_id(self) -> str:
        return f"decision-{uuid4().hex}"
