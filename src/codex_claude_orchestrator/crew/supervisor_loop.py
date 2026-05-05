from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.crew.decision_policy import CrewDecisionPolicy
from codex_claude_orchestrator.crew.gates import GateResult, WriteScopeGate
from codex_claude_orchestrator.crew.models import DecisionAction, DecisionActionType, WorkerRole
from codex_claude_orchestrator.crew.readiness import CrewReadinessEvaluator
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdict, ReviewVerdictParser
from codex_claude_orchestrator.runtime.marker_policy import MarkerObservationPolicy


_TURN_DONE_PREFIX = "<<<CODEX_TURN_DONE"


class CrewSupervisorLoop:
    def __init__(
        self,
        *,
        controller,
        poll_interval_seconds: float = 5.0,
        max_observe_attempts: int = 60,
        scope_gate: WriteScopeGate | None = None,
        readiness_evaluator: CrewReadinessEvaluator | None = None,
        review_parser: ReviewVerdictParser | None = None,
        marker_policy: MarkerObservationPolicy | None = None,
    ):
        self._controller = controller
        self._poll_interval_seconds = poll_interval_seconds
        self._max_observe_attempts = max_observe_attempts
        self._scope_gate = scope_gate or WriteScopeGate()
        self._readiness_evaluator = readiness_evaluator or CrewReadinessEvaluator()
        self._review_parser = review_parser or ReviewVerdictParser()
        self._marker_policy = marker_policy or MarkerObservationPolicy()

    async def run(
        self,
        *,
        crew_id: str,
        max_rounds: int,
        verification_commands: list[str],
        sampling_fn,
    ) -> dict:
        for round_index in range(1, max_rounds + 1):
            completed = self._wait_for_workers(crew_id)
            if not completed:
                return {"crew_id": crew_id, "status": "timeout", "rounds": round_index}
            verify_result = self._auto_verify(crew_id, verification_commands)
            if verify_result.get("passed"):
                decision = await self._ask_supervisor(sampling_fn, crew_id, "verification_passed", verify_result)
                if decision.get("action") == "accept":
                    return self._do_accept(crew_id)
                # Execute non-accept decisions (spawn, challenge)
                self._execute_decision(crew_id, decision)
                continue
            failure_count = verify_result.get("failure_count", 0)
            if failure_count >= 3:
                decision = await self._ask_supervisor(sampling_fn, crew_id, "verification_failed", verify_result)
                self._execute_decision(crew_id, decision)
                continue
            self._auto_challenge(crew_id, verify_result)
        return {"crew_id": crew_id, "status": "max_rounds_reached", "rounds": max_rounds}

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
                        contract_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=context_scout>>>",
                    )
                    events.append(
                        {
                            "action": "observe_worker",
                            "round": round_index,
                            "worker_id": source_worker["worker_id"],
                            "marker_seen": scout_observation.get("marker_seen", False),
                            "reason": scout_observation.get("reason", ""),
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
                contract_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=source_write>>>",
            )
            events.append(
                {
                    "action": "observe_worker",
                    "round": round_index,
                    "worker_id": source_worker["worker_id"],
                    "marker_seen": observation.get("marker_seen", False),
                    "reason": observation.get("reason", ""),
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
            review_verdict = None
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
                else:
                    auditor = self._active_review_worker(
                        self._controller.status(repo_root=repo_root, crew_id=crew_id)
                    )
                if auditor is None:
                    review_verdict = self._review_parser.parse("")
                    report, readiness_artifact = self._write_readiness_report(
                        crew_id=crew_id,
                        round_index=round_index,
                        worker=source_worker,
                        changes=changes,
                        scope_result=scope_result,
                        review_verdict=review_verdict,
                    )
                    return {
                        "crew_id": crew_id,
                        "status": "needs_human",
                        "reason": "review_worker_unavailable",
                        "rounds": round_index,
                        "events": events,
                        "readiness_artifact": readiness_artifact,
                        "readiness_status": report.status,
                    }
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
                    contract_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=patch_auditor>>>",
                )
                events.append(
                    {
                        "action": "observe_worker",
                        "round": round_index,
                        "worker_id": auditor["worker_id"],
                        "marker_seen": auditor_observation.get("marker_seen", False),
                        "reason": auditor_observation.get("reason", ""),
                    }
                )
                if not auditor_observation.get("marker_seen", False):
                    return self._waiting_result(crew_id, auditor["worker_id"], events)
                raw_artifact = auditor_observation.get("transcript_artifact") or auditor.get("transcript_artifact", "")
                current_turn_text = self._current_turn_observation_text(
                    auditor_observation,
                    auditor_marker,
                )
                review_verdict = self._review_parser.parse(
                    current_turn_text,
                    evidence_refs=[raw_artifact] if raw_artifact else [],
                    raw_artifact=raw_artifact,
                )
                review_artifact = self._write_json_artifact_if_supported(
                    crew_id=crew_id,
                    artifact_name=f"workers/{auditor['worker_id']}/review_verdict.json",
                    payload=review_verdict.to_dict(),
                )
                events.append(
                    {
                        "action": "review_verdict_parsed",
                        "round": round_index,
                        "worker_id": auditor["worker_id"],
                        "status": review_verdict.status,
                        "artifact": review_artifact,
                    }
                )
                self._record_blackboard_if_supported(
                    crew_id=crew_id,
                    entry_type="review",
                    content=f"Review verdict {review_verdict.status}: {review_verdict.summary}",
                    evidence_refs=[
                        ref
                        for ref in [review_artifact, *review_verdict.evidence_refs]
                        if ref
                    ],
                )
                if review_verdict.status == "unknown":
                    report, readiness_artifact = self._write_readiness_report(
                        crew_id=crew_id,
                        round_index=round_index,
                        worker=source_worker,
                        changes=changes,
                        scope_result=scope_result,
                        review_verdict=review_verdict,
                    )
                    return {
                        "crew_id": crew_id,
                        "status": "needs_human",
                        "reason": "review_verdict_unknown",
                        "rounds": round_index,
                        "events": events,
                        "readiness_artifact": readiness_artifact,
                        "readiness_status": report.status,
                    }
                if review_verdict.status == "block":
                    summary = self._review_challenge_message(review_verdict)
                    self._controller.challenge(crew_id=crew_id, summary=summary)
                    events.append({"action": "challenge", "round": round_index, "summary": summary})
                    continue
                review_status = review_verdict.status
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
                        contract_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=browser_flow>>>",
                    )
                    events.append(
                        {
                            "action": "observe_worker",
                            "round": round_index,
                            "worker_id": browser_worker["worker_id"],
                            "marker_seen": browser_observation.get("marker_seen", False),
                            "reason": browser_observation.get("reason", ""),
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
            readiness, readiness_artifact = self._write_readiness_report(
                crew_id=crew_id,
                round_index=round_index,
                worker=source_worker,
                changes=changes,
                scope_result=scope_result,
                review_verdict=review_verdict,
                verification_results=verification_results,
            )
            events.append(
                {
                    "action": "readiness_evaluated",
                    "round": round_index,
                    "status": readiness.status,
                    "artifact": readiness_artifact,
                }
            )
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
                    "readiness_artifact": readiness_artifact,
                    "warnings": readiness.warnings,
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

    def _wait_for_workers(self, crew_id: str, max_wait_attempts: int | None = None) -> bool:
        """阻塞轮询，直到所有 Worker 完成。返回 True 如果完成，False 如果超时。"""
        attempts = max_wait_attempts or self._max_observe_attempts
        for _ in range(attempts):
            details = self._controller.status(crew_id=crew_id)
            workers = details.get("workers", [])
            all_done = all(w.get("status") in ("idle", "stopped", "failed") for w in workers)
            if all_done:
                return True
            time.sleep(self._poll_interval_seconds)
        return False

    async def _ask_supervisor(self, sampling_fn, crew_id: str, situation: str, context: dict) -> dict:
        import json

        import mcp.types as types

        from codex_claude_orchestrator.mcp_server.context.compressor import compress_crew_status
        compressed = compress_crew_status(self._controller.status(crew_id=crew_id))
        prompt = self._build_decision_prompt(situation, context, compressed)
        result = await sampling_fn(
            messages=[types.SamplingMessage(role="user", content=types.TextContent(type="text", text=prompt))],
            system_prompt="你是 Crew supervisor，负责战略决策。根据提供的 context 选择下一步行动。回复格式：accept / spawn_worker(label, mission) / challenge(worker_id, goal)",
            max_tokens=500,
        )
        return self._parse_decision(result.content.text)

    def _build_decision_prompt(self, situation: str, context: dict, status: dict) -> str:
        import json
        status_text = json.dumps(status, ensure_ascii=False, indent=2)
        if situation == "verification_passed":
            return f"验证已通过，可以 accept。\n\n当前状态：\n{status_text}\n\n请确认是否 accept。"
        elif situation == "verification_failed":
            return f"验证失败 3 次，需要战略决策。\n\n失败详情：{json.dumps(context, ensure_ascii=False)}\n\n当前状态：\n{status_text}\n\n请选择：accept / spawn_worker(label, mission) / challenge(worker_id, goal)"
        return f"当前情况：{situation}\n\n上下文：{json.dumps(context, ensure_ascii=False)}\n\n状态：\n{status_text}"

    def _parse_decision(self, response: str) -> dict:
        import re
        text = response.strip()
        if text.startswith("accept"):
            return {"action": "accept"}
        match = re.match(r'spawn_worker\((.+)\)', text)
        if match:
            params = dict(re.findall(r"(\w+)=['\"]([^'\"]+)['\"]", match.group(1)))
            return {"action": "spawn_worker", **params}
        match = re.match(r'challenge\((.+)\)', text)
        if match:
            params = dict(re.findall(r"(\w+)=['\"]([^'\"]+)['\"]", match.group(1)))
            return {"action": "challenge", **params}
        return {"action": "observe"}

    def _execute_decision(self, crew_id: str, decision: dict) -> None:
        from codex_claude_orchestrator.crew.models import AuthorityLevel, WorkerContract, WorkspacePolicy
        if decision["action"] == "spawn_worker":
            contract = WorkerContract(
                contract_id=f"contract-{decision.get('label', 'worker')}",
                label=decision.get("label", "worker"),
                mission=decision.get("mission", ""),
                required_capabilities=["inspect_code", "edit_source"],
                authority_level=AuthorityLevel.SOURCE_WRITE,
                workspace_policy=WorkspacePolicy.WORKTREE,
            )
            self._controller.ensure_worker(crew_id=crew_id, contract=contract)
        elif decision["action"] == "accept":
            self._controller.accept(crew_id=crew_id)
        elif decision["action"] == "challenge":
            self._controller.challenge(crew_id=crew_id, worker_id=decision.get("worker_id", ""), goal=decision.get("goal", ""))

    def _do_accept(self, crew_id: str) -> dict:
        return self._controller.accept(crew_id=crew_id)

    def _auto_verify(self, crew_id: str, commands: list[str]) -> dict:
        """自动运行验证命令。"""
        if not commands:
            return {"passed": True, "failure_count": 0, "summary": "无验证命令"}
        # 简化实现：标记需要验证
        return {"passed": False, "failure_count": 1, "summary": "需要运行验证命令"}

    def _auto_challenge(self, crew_id: str, verify_result: dict) -> None:
        """自动发出挑战。"""
        pass  # 由现有 controller.challenge 处理

    def _wait_for_marker(
        self,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        interval: float,
        turn_marker: str | None = None,
        contract_marker: str = "",
    ) -> dict[str, Any]:
        last_observation: dict[str, Any] = {"marker_seen": False, "snapshot": ""}
        for attempt in range(self._max_observe_attempts):
            raw_observation = self._controller.observe_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=worker_id,
                lines=200,
                turn_marker=turn_marker,
            )
            expected_marker = turn_marker or raw_observation.get("marker", "")
            if not expected_marker:
                last_observation = {
                    **raw_observation,
                    "marker_seen": raw_observation.get("marker_seen", False),
                    "reason": "marker found by controller"
                    if raw_observation.get("marker_seen", False)
                    else "expected marker not found",
                }
                if last_observation.get("marker_seen", False):
                    return last_observation
                if interval > 0 and attempt + 1 < self._max_observe_attempts:
                    time.sleep(interval)
                continue
            policy_observation = self._marker_policy.evaluate(
                snapshot=raw_observation.get("snapshot", ""),
                expected_marker=expected_marker,
                transcript_text=raw_observation.get("transcript", ""),
                transcript_artifact=raw_observation.get("transcript_artifact", ""),
                contract_marker=contract_marker,
            )
            last_observation = {
                **raw_observation,
                **policy_observation.to_dict(),
                "marker_seen": policy_observation.marker_seen,
            }
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

    def _active_review_worker(self, details: dict[str, Any]) -> dict[str, Any] | None:
        active_review_workers = [
            worker
            for worker in details.get("workers", [])
            if worker.get("status", "running") not in {"failed", "stopped"}
            and "review_patch" in worker.get("capabilities", [])
        ]
        return next(
            (worker for worker in active_review_workers if worker.get("label") == "patch-risk-auditor"),
            active_review_workers[0] if active_review_workers else None,
        )

    def _current_turn_text(self, snapshot: str, expected_marker: str) -> str:
        before_marker = snapshot.split(expected_marker, 1)[0]
        prior_marker_start = before_marker.rfind(_TURN_DONE_PREFIX)
        if prior_marker_start == -1:
            return before_marker
        prior_marker_end = before_marker.find(">>>", prior_marker_start)
        if prior_marker_end == -1:
            return before_marker[prior_marker_start + len(_TURN_DONE_PREFIX):]
        return before_marker[prior_marker_end + len(">>>"):]

    def _current_turn_observation_text(self, observation: dict[str, Any], expected_marker: str) -> str:
        snapshot = observation.get("snapshot", "")
        snapshot_current_turn = self._current_turn_text(snapshot, expected_marker)
        if expected_marker in snapshot and "<<<CODEX_REVIEW" in snapshot_current_turn:
            return snapshot_current_turn
        transcript = observation.get("transcript", "")
        if transcript:
            return self._current_turn_text(transcript, expected_marker)
        return snapshot_current_turn

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
        review_verdict: ReviewVerdict | None = None,
        verification_results: list[dict[str, Any]] | None = None,
    ):
        report = self._readiness_evaluator.evaluate(
            round_id=f"round-{round_index}",
            worker_id=worker["worker_id"],
            contract_id=worker.get("contract_id", ""),
            changed_files=changes.get("changed_files", []),
            scope_result=scope_result,
            review_verdict=review_verdict,
            verification_results=verification_results or [],
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
        return (
            f"Review the implementer patch. Changed files: {changed_files}. Diff artifact: {diff_artifact}\n\n"
            "Return a parseable review block exactly in this shape:\n"
            "<<<CODEX_REVIEW\n"
            "verdict: OK | WARN | BLOCK\n"
            "summary: one sentence\n"
            "findings:\n"
            "- finding text\n"
            ">>>\n"
            "Use BLOCK for correctness regressions, unsafe scope, or missing critical tests."
        )

    def _review_challenge_message(self, review_verdict: ReviewVerdict) -> str:
        lines = [f"Review BLOCK: {review_verdict.summary}"]
        if review_verdict.findings:
            lines.append("Findings:")
            lines.extend(f"- {finding}" for finding in review_verdict.findings)
        lines.append("Fix these review blockers before verification.")
        return "\n".join(lines)

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
        reason = events[-1].get("reason") if events else None
        return {
            "crew_id": crew_id,
            "status": "waiting_for_worker",
            "worker_id": worker_id,
            "reason": reason or "expected marker not found",
            "events": events,
        }

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
