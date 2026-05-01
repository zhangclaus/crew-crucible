from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.crew.decision_policy import CrewDecisionPolicy
from codex_claude_orchestrator.crew.gates import GateResult, WriteScopeGate
from codex_claude_orchestrator.crew.models import DecisionAction, DecisionActionType, WorkerRole
from codex_claude_orchestrator.crew.readiness import CrewReadinessEvaluator
from codex_claude_orchestrator.workers.selection import WorkerSelectionPolicy


class CrewSupervisorLoop:
    def __init__(
        self,
        *,
        controller,
        poll_interval_seconds: float = 5.0,
        max_observe_attempts: int = 60,
        scope_gate: WriteScopeGate | None = None,
        readiness_evaluator: CrewReadinessEvaluator | None = None,
    ):
        self._controller = controller
        self._poll_interval_seconds = poll_interval_seconds
        self._max_observe_attempts = max_observe_attempts
        self._scope_gate = scope_gate or WriteScopeGate()
        self._readiness_evaluator = readiness_evaluator or CrewReadinessEvaluator()

    def run(
        self,
        *,
        repo_root: Path,
        goal: str,
        verification_commands: list[str],
        max_rounds: int = 3,
        worker_roles: list[WorkerRole] | None = None,
        poll_interval_seconds: float | None = None,
        allow_dirty_base: bool = False,
        spawn_policy: str = "static",
        seed_contract: str | None = None,
    ) -> dict[str, Any]:
        if spawn_policy == "dynamic":
            crew = self._controller.start_dynamic(repo_root=repo_root, goal=goal)
            return self.supervise_dynamic(
                repo_root=repo_root,
                crew_id=crew.crew_id,
                verification_commands=verification_commands,
                max_rounds=max_rounds,
                poll_interval_seconds=poll_interval_seconds,
                allow_dirty_base=allow_dirty_base,
                seed_contract=seed_contract,
            )
        selected_roles = worker_roles or WorkerSelectionPolicy().select(goal=goal).roles
        crew = self._controller.start(
            repo_root=repo_root,
            goal=goal,
            worker_roles=selected_roles,
            allow_dirty_base=allow_dirty_base,
        )
        return self.supervise(
            repo_root=repo_root,
            crew_id=crew.crew_id,
            verification_commands=verification_commands,
            max_rounds=max_rounds,
            poll_interval_seconds=poll_interval_seconds,
        )

    def supervise_dynamic(
        self,
        *,
        repo_root: Path,
        crew_id: str,
        verification_commands: list[str],
        max_rounds: int = 3,
        poll_interval_seconds: float | None = None,
        allow_dirty_base: bool = False,
        seed_contract: str | None = None,
    ) -> dict[str, Any]:
        if not verification_commands:
            raise ValueError("at least one verification command is required")
        interval = self._poll_interval_seconds if poll_interval_seconds is None else poll_interval_seconds
        policy = CrewDecisionPolicy()
        events: list[dict[str, Any]] = []
        verification_failures: list[dict[str, Any]] = []
        repo_write_scope = self._repo_write_scope(repo_root)
        pending_marker: str | None = None

        for round_index in range(1, max_rounds + 1):
            details = self._controller.status(repo_root=repo_root, crew_id=crew_id)
            source_worker = self._source_write_worker(details)
            startup_attempts = 0
            while source_worker is None:
                context_insufficient = seed_contract in {"context_scout", "readonly_scout"} and not self._has_worker_capability(
                    details,
                    "design_architecture",
                )
                action = policy.decide(
                    {
                        "crew_id": crew_id,
                        "goal": details.get("crew", {}).get("root_goal", ""),
                        "workers": details.get("workers", []),
                        "verification_failures": verification_failures,
                        "changed_files": [],
                        "seed_contract": seed_contract,
                        "context_insufficient": context_insufficient,
                        "repo_write_scope": repo_write_scope,
                    }
                )
                events.append({"action": action.action_type.value, "reason": action.reason})
                self._record_decision_if_supported(crew_id, action.to_dict())
                self._write_snapshot_if_supported(crew_id, action.to_dict())
                if action.action_type is not DecisionActionType.SPAWN_WORKER or action.contract is None:
                    return {"crew_id": crew_id, "status": "needs_human", "rounds": round_index - 1, "events": events}
                source_worker = self._controller.ensure_worker(
                    repo_root=repo_root,
                    crew_id=crew_id,
                    contract=action.contract,
                    allow_dirty_base=allow_dirty_base,
                )
                events.append(
                    {
                        "action": "spawn_worker",
                        "worker_id": source_worker["worker_id"],
                        "contract_id": action.contract.contract_id,
                        "label": action.contract.label,
                    }
                )
                if action.contract.label == "repo-context-scout":
                    scout_marker = self._turn_marker(crew_id, source_worker["worker_id"], "dynamic-context", round_index)
                    self._controller.send_worker(
                        repo_root=repo_root,
                        crew_id=crew_id,
                        worker_id=source_worker["worker_id"],
                        message="Inspect the repository context for this goal. Report relevant files, boundaries, risks, and smallest next step. Do not edit files.",
                        turn_marker=scout_marker,
                    )
                    events.append({"action": "send_worker", "worker_id": source_worker["worker_id"], "round": round_index})
                    scout_observation = self._wait_for_marker(
                        repo_root,
                        crew_id,
                        source_worker["worker_id"],
                        interval,
                        turn_marker=scout_marker,
                    )
                    events.append(
                        {
                            "action": "observe_worker",
                            "round": round_index,
                            "worker_id": source_worker["worker_id"],
                            "marker_seen": scout_observation.get("marker_seen", False),
                        }
                    )
                    if not scout_observation.get("marker_seen", False):
                        return self._waiting_result(crew_id, source_worker["worker_id"], events)
                    source_worker = None
                    seed_contract = None
                    details = self._controller.status(repo_root=repo_root, crew_id=crew_id)
                    startup_attempts += 1
                    if startup_attempts > 3:
                        return {"crew_id": crew_id, "status": "needs_human", "rounds": round_index - 1, "events": events}
                    continue

            pending_marker = self._turn_marker(crew_id, source_worker["worker_id"], "dynamic-implement", round_index)
            self._controller.send_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=source_worker["worker_id"],
                message="Begin or continue the dynamic worker contract. Report evidence, risks, and changed files.",
                turn_marker=pending_marker,
            )
            events.append({"action": "send_worker", "worker_id": source_worker["worker_id"], "round": round_index})

            observation = self._wait_for_marker(
                repo_root,
                crew_id,
                source_worker["worker_id"],
                interval,
                turn_marker=pending_marker,
            )
            events.append(
                {
                    "action": "observe_worker",
                    "round": round_index,
                    "worker_id": source_worker["worker_id"],
                    "marker_seen": observation.get("marker_seen", False),
                }
            )
            if not observation.get("marker_seen", False):
                return self._waiting_result(crew_id, source_worker["worker_id"], events)

            changes = self._controller.changes(crew_id=crew_id, worker_id=source_worker["worker_id"])
            events.append({"action": "record_changes", "changes": changes})
            scope_details = self._controller.status(repo_root=repo_root, crew_id=crew_id)
            write_scope = self._write_scope_for_worker(scope_details, source_worker, repo_write_scope)
            scope_evidence_refs = [
                ref
                for ref in (changes.get("artifact"), changes.get("diff_artifact"))
                if ref
            ]
            scope_result = self._scope_gate.evaluate(
                changed_files=changes.get("changed_files", []),
                write_scope=write_scope,
                evidence_refs=scope_evidence_refs,
            )
            scope_artifact = self._write_json_artifact_if_supported(
                crew_id=crew_id,
                artifact_name=f"gates/round-{round_index}/write_scope.json",
                payload={
                    **scope_result.to_dict(),
                    "worker_id": source_worker["worker_id"],
                    "contract_id": source_worker.get("contract_id", ""),
                    "changed_files": changes.get("changed_files", []),
                    "write_scope": write_scope,
                },
            )
            events.append(
                {
                    "action": "scope_gate",
                    "round": round_index,
                    "status": scope_result.status,
                    "artifact": scope_artifact,
                }
            )
            if scope_result.status == "block":
                report, readiness_artifact = self._write_readiness_report(
                    crew_id=crew_id,
                    round_index=round_index,
                    worker=source_worker,
                    changes=changes,
                    scope_result=scope_result,
                )
                return {
                    "crew_id": crew_id,
                    "status": "needs_human",
                    "reason": "write_scope_blocked",
                    "rounds": round_index,
                    "events": events,
                    "readiness_artifact": readiness_artifact,
                    "readiness_status": report.status,
                }
            if scope_result.status == "challenge":
                summary = self._scope_challenge_message(scope_result)
                self._controller.challenge(crew_id=crew_id, summary=summary)
                events.append({"action": "challenge", "round": round_index, "summary": summary})
                continue
            review_status = None
            if changes.get("changed_files"):
                review_action = policy.decide(
                    {
                        "crew_id": crew_id,
                        "goal": details.get("crew", {}).get("root_goal", ""),
                        "workers": self._controller.status(repo_root=repo_root, crew_id=crew_id).get("workers", []),
                        "changed_files": changes.get("changed_files", []),
                        "review_status": review_status,
                        "verification_failures": verification_failures,
                        "repo_write_scope": repo_write_scope,
                    }
                )
                if review_action.action_type is DecisionActionType.SPAWN_WORKER and review_action.contract is not None:
                    self._record_decision_if_supported(crew_id, review_action.to_dict())
                    auditor = self._controller.ensure_worker(
                        repo_root=repo_root,
                        crew_id=crew_id,
                        contract=review_action.contract,
                        allow_dirty_base=False,
                    )
                    events.append(
                        {
                            "action": "spawn_worker",
                            "worker_id": auditor["worker_id"],
                            "contract_id": review_action.contract.contract_id,
                            "label": review_action.contract.label,
                            "reason": review_action.reason,
                        }
                    )
                    auditor_marker = self._turn_marker(crew_id, auditor["worker_id"], "dynamic-review", round_index)
                    self._controller.send_worker(
                        repo_root=repo_root,
                        crew_id=crew_id,
                        worker_id=auditor["worker_id"],
                        message=self._review_message(changes),
                        turn_marker=auditor_marker,
                    )
                    events.append({"action": "send_worker", "worker_id": auditor["worker_id"], "round": round_index})
                    auditor_observation = self._wait_for_marker(
                        repo_root,
                        crew_id,
                        auditor["worker_id"],
                        interval,
                        turn_marker=auditor_marker,
                    )
                    events.append(
                        {
                            "action": "observe_worker",
                            "round": round_index,
                            "worker_id": auditor["worker_id"],
                            "marker_seen": auditor_observation.get("marker_seen", False),
                        }
                    )
                    if not auditor_observation.get("marker_seen", False):
                        return self._waiting_result(crew_id, auditor["worker_id"], events)
                    review_status = "ok"
                    browser_action = policy.decide(
                        {
                            "crew_id": crew_id,
                            "goal": details.get("crew", {}).get("root_goal", ""),
                            "workers": self._controller.status(repo_root=repo_root, crew_id=crew_id).get("workers", []),
                            "changed_files": changes.get("changed_files", []),
                            "review_status": review_status,
                            "browser_check_status": None,
                            "verification_failures": verification_failures,
                            "repo_write_scope": repo_write_scope,
                        }
                    )
                    if browser_action.action_type is DecisionActionType.SPAWN_WORKER and browser_action.contract is not None:
                        self._record_decision_if_supported(crew_id, browser_action.to_dict())
                        browser_worker = self._controller.ensure_worker(
                            repo_root=repo_root,
                            crew_id=crew_id,
                            contract=browser_action.contract,
                            allow_dirty_base=False,
                        )
                        events.append(
                            {
                                "action": "spawn_worker",
                                "worker_id": browser_worker["worker_id"],
                                "contract_id": browser_action.contract.contract_id,
                                "label": browser_action.contract.label,
                                "reason": browser_action.reason,
                            }
                        )
                        browser_marker = self._turn_marker(crew_id, browser_worker["worker_id"], "dynamic-browser", round_index)
                        self._controller.send_worker(
                            repo_root=repo_root,
                            crew_id=crew_id,
                            worker_id=browser_worker["worker_id"],
                            message=self._browser_message(changes),
                            turn_marker=browser_marker,
                        )
                        events.append({"action": "send_worker", "worker_id": browser_worker["worker_id"], "round": round_index})
                        browser_observation = self._wait_for_marker(
                            repo_root,
                            crew_id,
                            browser_worker["worker_id"],
                            interval,
                            turn_marker=browser_marker,
                        )
                        events.append(
                            {
                                "action": "observe_worker",
                                "round": round_index,
                                "worker_id": browser_worker["worker_id"],
                                "marker_seen": browser_observation.get("marker_seen", False),
                            }
                        )
                        if not browser_observation.get("marker_seen", False):
                            return self._waiting_result(crew_id, browser_worker["worker_id"], events)
            verification_results = [
                self._controller.verify(
                    crew_id=crew_id,
                    command=command,
                    worker_id=source_worker["worker_id"],
                )
                for command in verification_commands
            ]
            events.append({"action": "verify", "round": round_index, "results": verification_results})
            failed = [result for result in verification_results if not result.get("passed", False)]
            if not failed:
                accept_action = DecisionAction(
                    action_id=f"decision-accept-{crew_id}-{round_index}",
                    crew_id=crew_id,
                    action_type=DecisionActionType.ACCEPT_READY,
                    reason="verification passed and dynamic checks completed",
                    priority=100,
                )
                self._record_decision_if_supported(crew_id, accept_action.to_dict())
                self._write_snapshot_if_supported(crew_id, accept_action.to_dict())
                return {
                    "crew_id": crew_id,
                    "status": "ready_for_codex_accept",
                    "rounds": round_index,
                    "events": events,
                }

            verification_failures.extend(failed)
            summary = "; ".join(result.get("summary", "verification failed") for result in failed)
            self._controller.challenge(crew_id=crew_id, summary=summary)
            events.append({"action": "challenge", "round": round_index, "summary": summary})

            if len(verification_failures) >= 2:
                action = policy.decide(
                    {
                        "crew_id": crew_id,
                        "goal": details.get("crew", {}).get("root_goal", ""),
                        "workers": self._controller.status(repo_root=repo_root, crew_id=crew_id).get("workers", []),
                        "verification_failures": verification_failures,
                        "changed_files": changes.get("changed_files", []),
                        "review_status": "warn",
                        "repo_write_scope": repo_write_scope,
                    }
                )
                self._record_decision_if_supported(crew_id, action.to_dict())
                self._write_snapshot_if_supported(crew_id, action.to_dict())
                if action.action_type is DecisionActionType.SPAWN_WORKER and action.contract is not None:
                    analyst = self._controller.ensure_worker(
                        repo_root=repo_root,
                        crew_id=crew_id,
                        contract=action.contract,
                        allow_dirty_base=False,
                    )
                    events.append(
                        {
                            "action": "spawn_worker",
                            "worker_id": analyst["worker_id"],
                            "contract_id": action.contract.contract_id,
                            "label": action.contract.label,
                            "reason": action.reason,
                        }
                    )
                    if action.contract.label == "guardrail-maintainer":
                        self._append_known_pitfall_if_supported(
                            crew_id=crew_id,
                            failures=verification_failures,
                            evidence_refs=action.contract.context_refs,
                        )

        return {"crew_id": crew_id, "status": "max_rounds_exhausted", "rounds": max_rounds, "events": events}

    def supervise(
        self,
        *,
        repo_root: Path,
        crew_id: str,
        verification_commands: list[str],
        max_rounds: int = 3,
        poll_interval_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not verification_commands:
            raise ValueError("at least one verification command is required")
        interval = self._poll_interval_seconds if poll_interval_seconds is None else poll_interval_seconds
        details = self._controller.status(repo_root=repo_root, crew_id=crew_id)
        explorer = self._worker_by_role(details, WorkerRole.EXPLORER)
        implementer = self._worker_by_role(details, WorkerRole.IMPLEMENTER)
        reviewer = self._worker_by_role(details, WorkerRole.REVIEWER)
        if implementer is None:
            raise ValueError(f"crew {crew_id} has no implementer worker")

        events = []
        if explorer is not None:
            explorer_observation = self._wait_for_marker(repo_root, crew_id, explorer["worker_id"], interval)
            events.append({"action": "observe_explorer", "marker_seen": explorer_observation.get("marker_seen", False)})
            if not explorer_observation.get("marker_seen", False):
                return self._waiting_result(crew_id, explorer["worker_id"], events)
            self._controller.send_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=implementer["worker_id"],
                message="Use the explorer findings in the blackboard and continue implementation.",
                turn_marker=self._turn_marker(crew_id, implementer["worker_id"], "implement", 1),
            )
            events.append({"action": "send_explorer_context", "worker_id": implementer["worker_id"]})
            pending_implementer_marker = self._turn_marker(crew_id, implementer["worker_id"], "implement", 1)
        else:
            pending_implementer_marker = self._turn_marker(crew_id, implementer["worker_id"], "implement", 1)
            self._controller.send_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=implementer["worker_id"],
                message="Begin implementation now and report evidence, risks, and changed files.",
                turn_marker=pending_implementer_marker,
            )
            events.append({"action": "send_begin_implementation", "worker_id": implementer["worker_id"]})

        for round_index in range(1, max_rounds + 1):
            implementer_observation = self._wait_for_marker(
                repo_root,
                crew_id,
                implementer["worker_id"],
                interval,
                turn_marker=pending_implementer_marker,
            )
            events.append(
                {
                    "action": "observe_implementer",
                    "round": round_index,
                    "marker_seen": implementer_observation.get("marker_seen", False),
                }
            )
            if not implementer_observation.get("marker_seen", False):
                return self._waiting_result(crew_id, implementer["worker_id"], events)

            changes = self._controller.changes(crew_id=crew_id, worker_id=implementer["worker_id"])
            events.append({"action": "record_changes", "changes": changes})

            if reviewer is not None:
                reviewer_marker = self._turn_marker(crew_id, reviewer["worker_id"], "review", round_index)
                self._controller.send_worker(
                    repo_root=repo_root,
                    crew_id=crew_id,
                    worker_id=reviewer["worker_id"],
                    message=self._review_message(changes),
                    turn_marker=reviewer_marker,
                )
                reviewer_observation = self._wait_for_marker(
                    repo_root,
                    crew_id,
                    reviewer["worker_id"],
                    interval,
                    turn_marker=reviewer_marker,
                )
                events.append(
                    {
                        "action": "observe_reviewer",
                        "round": round_index,
                        "marker_seen": reviewer_observation.get("marker_seen", False),
                    }
                )
                if not reviewer_observation.get("marker_seen", False):
                    return self._waiting_result(crew_id, reviewer["worker_id"], events)

            verification_results = [
                self._controller.verify(
                    crew_id=crew_id,
                    command=command,
                    worker_id=implementer["worker_id"],
                )
                for command in verification_commands
            ]
            events.append({"action": "verify", "round": round_index, "results": verification_results})
            failed = [result for result in verification_results if not result.get("passed", False)]
            if not failed:
                return {
                    "crew_id": crew_id,
                    "status": "ready_for_codex_accept",
                    "rounds": round_index,
                    "events": events,
                }

            summary = "; ".join(result.get("summary", "verification failed") for result in failed)
            self._controller.challenge(crew_id=crew_id, summary=summary)
            self._controller.send_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=implementer["worker_id"],
                message=f"Fix verification failure before the next Codex review:\n{summary}",
                turn_marker=self._turn_marker(crew_id, implementer["worker_id"], "repair", round_index + 1),
            )
            pending_implementer_marker = self._turn_marker(crew_id, implementer["worker_id"], "repair", round_index + 1)
            events.append({"action": "challenge_implementer", "round": round_index, "summary": summary})

        return {"crew_id": crew_id, "status": "max_rounds_exhausted", "rounds": max_rounds, "events": events}

    def _wait_for_marker(
        self,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        interval: float,
        turn_marker: str | None = None,
    ) -> dict[str, Any]:
        last_observation: dict[str, Any] = {"marker_seen": False, "snapshot": ""}
        for attempt in range(self._max_observe_attempts):
            last_observation = self._controller.observe_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=worker_id,
                lines=200,
                turn_marker=turn_marker,
            )
            if last_observation.get("marker_seen", False):
                return last_observation
            if interval > 0 and attempt + 1 < self._max_observe_attempts:
                time.sleep(interval)
        return last_observation

    def _worker_by_role(self, details: dict[str, Any], role: WorkerRole) -> dict[str, Any] | None:
        return next((worker for worker in details.get("workers", []) if worker.get("role") == role.value), None)

    def _source_write_worker(self, details: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in details.get("workers", [])
                if item.get("authority_level") == "source_write"
                and item.get("status", "running") not in {"failed", "stopped"}
            ),
            None,
        )

    def _has_worker_capability(self, details: dict[str, Any], capability: str) -> bool:
        return any(
            worker.get("status", "running") not in {"failed", "stopped"}
            and capability in worker.get("capabilities", [])
            for worker in details.get("workers", [])
        )

    def _repo_write_scope(self, repo_root: Path) -> list[str]:
        common_roots = ("src", "tests", "test", "tools", "packages", "apps", "app", "lib", "scripts")
        nested_test_parents = ("src", "tools", "packages", "apps", "app", "lib")
        candidates: list[str] = []

        for name in common_roots:
            if (repo_root / name).is_dir():
                candidates.append(f"{name}/")

        for parent in nested_test_parents:
            for child in ("tests", "test"):
                if (repo_root / parent / child).is_dir():
                    candidates.append(f"{parent}/{child}/")

        return list(dict.fromkeys(candidates)) or ["src/", "tests/"]

    def _write_snapshot_if_supported(self, crew_id: str, last_decision: dict[str, Any]) -> None:
        writer = getattr(self._controller, "write_team_snapshot", None)
        if writer is not None:
            writer(crew_id=crew_id, last_decision=last_decision)

    def _record_decision_if_supported(self, crew_id: str, action: dict[str, Any]) -> None:
        recorder = getattr(self._controller, "record_decision", None)
        if recorder is not None:
            recorder(crew_id=crew_id, action=action)

    def _write_json_artifact_if_supported(self, *, crew_id: str, artifact_name: str, payload: Any) -> str:
        writer = getattr(self._controller, "write_json_artifact", None)
        if writer is not None:
            return writer(crew_id=crew_id, artifact_name=artifact_name, payload=payload)
        return artifact_name

    def _record_blackboard_if_supported(
        self,
        *,
        crew_id: str,
        entry_type: str,
        content: str,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any] | None:
        recorder = getattr(self._controller, "record_blackboard_entry", None)
        if recorder is None:
            return None
        return recorder(
            crew_id=crew_id,
            entry_type=entry_type,
            content=content,
            evidence_refs=evidence_refs or [],
        )

    def _write_scope_for_worker(
        self,
        details: dict[str, Any],
        worker: dict[str, Any],
        fallback_scope: list[str],
    ) -> list[str]:
        worker_scope = worker.get("write_scope") or []
        if worker_scope:
            return list(worker_scope)

        contract_id = worker.get("contract_id")
        for contract in details.get("worker_contracts", []):
            if contract.get("contract_id") == contract_id and contract.get("write_scope"):
                return list(contract["write_scope"])

        return list(fallback_scope)

    def _write_readiness_report(
        self,
        *,
        crew_id: str,
        round_index: int,
        worker: dict[str, Any],
        changes: dict[str, Any],
        scope_result: GateResult,
    ):
        report = self._readiness_evaluator.evaluate(
            round_id=f"round-{round_index}",
            worker_id=worker["worker_id"],
            contract_id=worker.get("contract_id", ""),
            changed_files=changes.get("changed_files", []),
            scope_result=scope_result,
            review_verdict=None,
            verification_results=[],
        )
        artifact_name = self._write_json_artifact_if_supported(
            crew_id=crew_id,
            artifact_name=f"readiness/round-{round_index}.json",
            payload=report.to_dict(),
        )
        self._record_blackboard_if_supported(
            crew_id=crew_id,
            entry_type="decision",
            content=f"Readiness {report.status}: write scope {scope_result.status}",
            evidence_refs=[artifact_name],
        )
        return report, artifact_name

    def _scope_challenge_message(self, scope_result: GateResult) -> str:
        out_of_scope = scope_result.details.get("out_of_scope", [])
        changed = ", ".join(out_of_scope) or "unknown files"
        return f"Changed files outside write_scope: {changed}. Update the patch to stay within scope or explain why scope must change."

    def _review_message(self, changes: dict[str, Any]) -> str:
        changed_files = ", ".join(changes.get("changed_files", [])) or "no changed files"
        diff_artifact = changes.get("diff_artifact", "")
        return f"Review the implementer patch. Changed files: {changed_files}. Diff artifact: {diff_artifact}"

    def _browser_message(self, changes: dict[str, Any]) -> str:
        changed_files = ", ".join(changes.get("changed_files", [])) or "no changed files"
        diff_artifact = changes.get("diff_artifact", "")
        return (
            "Verify the changed browser/user flow if a local app is available. "
            f"Changed files: {changed_files}. Diff artifact: {diff_artifact}. "
            "Report pass/fail, visible regressions, and reproduction steps."
        )

    def _turn_marker(self, crew_id: str, worker_id: str, phase: str, round_index: int) -> str:
        return f"<<<CODEX_TURN_DONE crew={crew_id} worker={worker_id} phase={phase} round={round_index}>>>"

    def _waiting_result(self, crew_id: str, worker_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        return {"crew_id": crew_id, "status": "waiting_for_worker", "worker_id": worker_id, "events": events}

    def _append_known_pitfall_if_supported(
        self,
        *,
        crew_id: str,
        failures: list[dict[str, Any]],
        evidence_refs: list[str],
    ) -> None:
        appender = getattr(self._controller, "append_known_pitfall", None)
        if appender is None:
            return
        summary = "; ".join(failure.get("summary", "verification failed") for failure in failures[-3:])
        appender(
            crew_id=crew_id,
            failure_class="verification_repeat",
            summary=summary,
            guardrail="Stop silent retries after three similar verification failures; classify the failure and create a focused guardrail.",
            evidence_refs=evidence_refs,
        )
