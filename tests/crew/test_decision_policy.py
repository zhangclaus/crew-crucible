from codex_claude_orchestrator.crew.models import AuthorityLevel, DecisionActionType
from codex_claude_orchestrator.crew.decision_policy import CrewDecisionPolicy


def test_decision_policy_spawns_source_write_contract_when_goal_requires_edits_and_no_worker_active():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Fix the failing pytest tests",
            "workers": [],
            "verification_failures": [],
            "changed_files": [],
        }
    )

    assert action.action_type == DecisionActionType.SPAWN_WORKER
    assert action.contract is not None
    assert action.contract.label == "targeted-code-editor"
    assert action.contract.authority_level == AuthorityLevel.SOURCE_WRITE
    assert action.contract.required_capabilities == ["inspect_code", "edit_source", "edit_tests", "run_verification"]


def test_decision_policy_uses_repo_write_scope_for_source_contract():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Implement the tools package changes",
            "workers": [],
            "verification_failures": [],
            "changed_files": [],
            "repo_write_scope": ["tools/", "tools/tests/"],
        }
    )

    assert action.action_type == DecisionActionType.SPAWN_WORKER
    assert action.contract is not None
    assert action.contract.write_scope == ["tools/", "tools/tests/"]


def test_decision_policy_requests_patch_auditor_when_patch_exists_without_review():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Refactor the public API",
            "workers": [
                {
                    "worker_id": "worker-source",
                    "status": "running",
                    "capabilities": ["inspect_code", "edit_source", "edit_tests", "run_verification"],
                    "authority_level": "source_write",
                }
            ],
            "changed_files": ["src/public_api.py", "tests/test_public_api.py"],
            "review_status": None,
            "verification_failures": [],
        }
    )

    assert action.action_type == DecisionActionType.SPAWN_WORKER
    assert action.contract is not None
    assert action.contract.label == "patch-risk-auditor"
    assert action.contract.required_capabilities == ["review_patch", "inspect_code"]
    assert action.contract.authority_level == AuthorityLevel.READONLY


def test_decision_policy_spawns_failure_analyst_after_repeated_verification_failures():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Fix the failing pytest tests",
            "workers": [
                {
                    "worker_id": "worker-source",
                    "status": "running",
                    "capabilities": ["inspect_code", "edit_source", "edit_tests", "run_verification"],
                    "authority_level": "source_write",
                }
            ],
            "changed_files": ["src/app.py"],
            "review_status": "warn",
            "verification_failures": [
                {"summary": "pytest failed"},
                {"summary": "pytest failed again"},
            ],
        }
    )

    assert action.action_type == DecisionActionType.SPAWN_WORKER
    assert action.contract is not None
    assert action.contract.label == "verification-failure-analyst"
    assert action.contract.required_capabilities == ["triage_failure", "inspect_code"]


def test_decision_policy_accepts_when_source_worker_patch_review_and_verification_are_ready():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Fix the failing pytest tests",
            "workers": [
                {
                    "worker_id": "worker-source",
                    "status": "running",
                    "capabilities": ["inspect_code", "edit_source", "edit_tests", "run_verification"],
                    "authority_level": "source_write",
                }
            ],
            "changed_files": ["src/app.py"],
            "review_status": "ok",
            "verification_failures": [],
            "verification_passed": True,
        }
    )

    assert action.action_type == DecisionActionType.ACCEPT_READY


def test_decision_policy_spawns_browser_tester_for_ui_goal_after_patch_review():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Update the browser UI checkout flow",
            "workers": [
                {
                    "worker_id": "worker-source",
                    "status": "running",
                    "capabilities": ["inspect_code", "edit_source", "edit_tests", "run_verification"],
                    "authority_level": "source_write",
                }
            ],
            "changed_files": ["src/App.tsx"],
            "review_status": "ok",
            "browser_check_status": None,
            "verification_failures": [],
        }
    )

    assert action.action_type == DecisionActionType.SPAWN_WORKER
    assert action.contract is not None
    assert action.contract.label == "browser-flow-tester"
    assert action.contract.required_capabilities == ["browser_e2e", "review_patch"]
    assert action.contract.authority_level == AuthorityLevel.READONLY


def test_decision_policy_spawns_browser_tester_for_frontend_repo_risk_after_patch_review():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Adjust checkout behavior",
            "workers": [
                {
                    "worker_id": "worker-source",
                    "status": "running",
                    "capabilities": ["inspect_code", "edit_source", "edit_tests", "run_verification"],
                    "authority_level": "source_write",
                }
            ],
            "changed_files": ["apps/web/src/Checkout.tsx"],
            "review_status": "ok",
            "browser_check_status": None,
            "verification_failures": [],
            "repo_risk_tags": ["frontend"],
        }
    )

    assert action.action_type == DecisionActionType.SPAWN_WORKER
    assert action.contract is not None
    assert action.contract.label == "browser-flow-tester"


def test_decision_policy_spawns_guardrail_maintainer_after_three_failures_when_failure_analyst_exists():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Fix repeated verification failures",
            "workers": [
                {
                    "worker_id": "worker-source",
                    "status": "running",
                    "capabilities": ["inspect_code", "edit_source", "edit_tests", "run_verification"],
                    "authority_level": "source_write",
                },
                {
                    "worker_id": "worker-analyst",
                    "status": "running",
                    "capabilities": ["triage_failure", "inspect_code"],
                    "authority_level": "readonly",
                },
            ],
            "changed_files": ["src/app.py"],
            "review_status": "warn",
            "verification_failures": [
                {"summary": "pytest failed 1"},
                {"summary": "pytest failed 2"},
                {"summary": "pytest failed 3"},
            ],
        }
    )

    assert action.action_type == DecisionActionType.SPAWN_WORKER
    assert action.contract is not None
    assert action.contract.label == "guardrail-maintainer"
    assert action.contract.required_capabilities == ["maintain_guardrails", "triage_failure", "inspect_code"]


def test_decision_policy_spawns_readonly_context_scout_when_context_is_insufficient():
    action = CrewDecisionPolicy().decide(
        {
            "crew_id": "crew-1",
            "goal": "Implement a risky architecture change",
            "workers": [],
            "changed_files": [],
            "verification_failures": [],
            "context_insufficient": True,
        }
    )

    assert action.action_type == DecisionActionType.SPAWN_WORKER
    assert action.contract is not None
    assert action.contract.label == "repo-context-scout"
    assert action.contract.required_capabilities == ["inspect_code", "design_architecture"]
    assert action.contract.authority_level == AuthorityLevel.READONLY
