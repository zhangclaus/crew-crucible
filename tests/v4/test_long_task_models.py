"""Tests for long task data models."""

from __future__ import annotations

import json
from pathlib import Path

from codex_claude_orchestrator.v4.long_task_models import (
    ApiSpec,
    AutoFix,
    Briefing,
    ChallengeTarget,
    CheckItem,
    Contract,
    DataModel,
    PlanAdversaryVerdict,
    PlanIssue,
    ProjectContext,
    ReviewVerdict,
    StagePlan,
    SubTaskRef,
    ThinkResult,
)


class TestContract:
    def test_contract_defaults(self):
        c = Contract()
        assert c.api_endpoints == []
        assert c.data_models == []
        assert c.shared_types == []
        assert c.conventions == []

    def test_contract_to_dict_roundtrip(self):
        c = Contract(
            api_endpoints=[ApiSpec(method="POST", path="/api/login", response_body={"token": "str"})],
            data_models=[DataModel(name="User", fields={"id": "int", "email": "str"})],
            shared_types=["AuthToken"],
            conventions=["use snake_case"],
        )
        d = c.to_dict()
        assert d["api_endpoints"][0]["method"] == "POST"
        assert d["data_models"][0]["name"] == "User"
        c2 = Contract.from_dict(d)
        assert c2.api_endpoints[0].method == "POST"
        assert c2.data_models[0].name == "User"
        assert c2.shared_types == ["AuthToken"]

    def test_contract_to_json(self):
        c = Contract(conventions=["use pytest"])
        j = c.to_json()
        parsed = json.loads(j)
        assert parsed["conventions"] == ["use pytest"]


class TestApiSpec:
    def test_api_spec_defaults(self):
        a = ApiSpec(method="GET", path="/api/users")
        assert a.request_body is None
        assert a.response_body == {}
        assert a.description == ""

    def test_api_spec_to_dict(self):
        a = ApiSpec(
            method="POST",
            path="/api/auth/login",
            request_body={"email": "str", "password": "str"},
            response_body={"token": "str"},
            description="Login endpoint",
        )
        d = a.to_dict()
        assert d["method"] == "POST"
        assert d["request_body"]["email"] == "str"
        a2 = ApiSpec.from_dict(d)
        assert a2.method == "POST"
        assert a2.description == "Login endpoint"


class TestDataModel:
    def test_data_model_to_dict_roundtrip(self):
        dm = DataModel(name="User", fields={"id": "int", "email": "str", "active": "bool"})
        d = dm.to_dict()
        dm2 = DataModel.from_dict(d)
        assert dm2.name == "User"
        assert dm2.fields == {"id": "int", "email": "str", "active": "bool"}


class TestProjectContext:
    def test_project_context_defaults(self):
        pc = ProjectContext()
        assert pc.structure == ""
        assert pc.existing_patterns == []
        assert pc.tech_stack == []
        assert pc.related_files == []
        assert pc.constraints == []

    def test_project_context_to_dict_roundtrip(self):
        pc = ProjectContext(
            structure="src/auth/, src/api/",
            existing_patterns=["FastAPI", "SQLAlchemy"],
            tech_stack=["Python 3.11", "FastAPI"],
            related_files=["src/auth/"],
            constraints=["不能改数据库 schema"],
        )
        d = pc.to_dict()
        pc2 = ProjectContext.from_dict(d)
        assert pc2.structure == "src/auth/, src/api/"
        assert pc2.tech_stack == ["Python 3.11", "FastAPI"]
        assert pc2.constraints == ["不能改数据库 schema"]


class TestSubTaskRef:
    """SubTaskRef is a lightweight reference used in StagePlan.sub_tasks.
    It extends the concept of SubTask with role, goal, write_scope, worker_template.
    """

    def test_subtask_ref_defaults(self):
        st = SubTaskRef(task_id="1a", role="backend-developer", goal="实现 API")
        assert st.dependencies == []
        assert st.write_scope == []
        assert st.worker_template == "targeted-code-editor"

    def test_subtask_ref_to_dict_roundtrip(self):
        st = SubTaskRef(
            task_id="2a",
            role="frontend-developer",
            goal="实现登录页面",
            dependencies=["2a-api"],
            write_scope=["src/pages/login.tsx", "src/hooks/useAuth.ts"],
            worker_template="frontend-developer",
        )
        d = st.to_dict()
        assert d["task_id"] == "2a"
        assert d["role"] == "frontend-developer"
        assert d["write_scope"] == ["src/pages/login.tsx", "src/hooks/useAuth.ts"]
        st2 = SubTaskRef.from_dict(d)
        assert st2.task_id == "2a"
        assert st2.worker_template == "frontend-developer"


class TestStagePlan:
    def test_stage_plan_to_dict_roundtrip(self):
        sp = StagePlan(
            stage_id=1,
            goal="实现认证功能",
            acceptance_criteria=["支持 RS256", "token 过期 30 分钟"],
            contract=Contract(api_endpoints=[ApiSpec(method="POST", path="/api/auth/login")]),
            sub_tasks=[
                SubTaskRef(task_id="1a", role="backend-developer", goal="实现 JWT API"),
                SubTaskRef(task_id="1b", role="frontend-developer", goal="实现登录页面", dependencies=["1a"]),
            ],
            dependencies=[],
        )
        d = sp.to_dict()
        assert d["stage_id"] == 1
        assert d["goal"] == "实现认证功能"
        assert len(d["sub_tasks"]) == 2
        assert d["sub_tasks"][1]["dependencies"] == ["1a"]
        sp2 = StagePlan.from_dict(d)
        assert sp2.stage_id == 1
        assert sp2.contract.api_endpoints[0].method == "POST"
        assert len(sp2.sub_tasks) == 2

    def test_stage_plan_event_dict(self):
        sp = StagePlan(
            stage_id=2,
            goal="测试",
            acceptance_criteria=["pytest 通过"],
            contract=Contract(),
            sub_tasks=[SubTaskRef(task_id="2a", role="test-writer", goal="写测试")],
            dependencies=[1],
        )
        ed = sp.to_event_dict()
        assert ed["stage_id"] == 2
        assert ed["dependencies"] == [1]
        assert ed["sub_tasks"][0]["task_id"] == "2a"


class TestThinkResult:
    def test_think_result_to_dict_roundtrip(self):
        tr = ThinkResult(
            spec="重构认证模块...",
            stages=[
                StagePlan(
                    stage_id=1,
                    goal="探索",
                    acceptance_criteria=["列出文件"],
                    contract=Contract(),
                    sub_tasks=[SubTaskRef(task_id="1a", role="repo-context-scout", goal="探索代码")],
                    dependencies=[],
                )
            ],
            contract=Contract(conventions=["use pytest"]),
            project_context=ProjectContext(tech_stack=["Python 3.11"]),
            acceptance_criteria=["所有测试通过"],
            open_questions=["是否需要支持 OAuth?"],
        )
        d = tr.to_dict()
        assert d["spec"] == "重构认证模块..."
        assert len(d["stages"]) == 1
        assert d["stages"][0]["goal"] == "探索"
        assert d["contract"]["conventions"] == ["use pytest"]
        assert d["project_context"]["tech_stack"] == ["Python 3.11"]
        tr2 = ThinkResult.from_dict(d)
        assert tr2.spec == "重构认证模块..."
        assert tr2.stages[0].goal == "探索"
        assert tr2.open_questions == ["是否需要支持 OAuth?"]

    def test_think_result_from_dict_missing_fields_raises(self):
        import pytest
        with pytest.raises(KeyError):
            ThinkResult.from_dict({"spec": "test"})


class TestBriefing:
    def test_briefing_to_dict_roundtrip(self):
        b = Briefing(
            overall_goal="重构认证",
            current_stage=StagePlan(
                stage_id=1,
                goal="实现 JWT",
                acceptance_criteria=["RS256"],
                contract=Contract(),
                sub_tasks=[],
                dependencies=[],
            ),
            contract=Contract(conventions=["snake_case"]),
            previous_summaries=["阶段 0: 探索完成"],
            key_decisions=["用 RS256"],
            constraints=["不改 DB"],
            pending_questions=["OAuth?"],
            verification_commands=["pytest"],
        )
        d = b.to_dict()
        assert d["overall_goal"] == "重构认证"
        assert d["previous_summaries"] == ["阶段 0: 探索完成"]
        assert d["verification_commands"] == ["pytest"]
        b2 = Briefing.from_dict(d)
        assert b2.overall_goal == "重构认证"
        assert b2.current_stage.stage_id == 1


class TestReviewVerdict:
    def test_review_verdict_pass(self):
        rv = ReviewVerdict(
            verdict="OK",
            checklist=[CheckItem(criterion="RS256", status="pass", note="已实现")],
            quality_notes=["代码清晰"],
            risks=["未处理 token 刷新"],
            suggestions=["加 rate limiting"],
            contract_compliance=[CheckItem(criterion="POST /api/login", status="pass", note="正确")],
            cross_worker_issues=[],
            action="pass",
            challenge_targets=None,
            replan_reason=None,
            stage_summary="实现了 JWT 认证，测试通过",
        )
        d = rv.to_dict()
        assert d["action"] == "pass"
        assert d["verdict"] == "OK"
        assert d["stage_summary"] == "实现了 JWT 认证，测试通过"
        rv2 = ReviewVerdict.from_dict(d)
        assert rv2.action == "pass"
        assert rv2.checklist[0].status == "pass"

    def test_review_verdict_challenge(self):
        rv = ReviewVerdict(
            verdict="WARN",
            checklist=[],
            quality_notes=[],
            risks=[],
            suggestions=[],
            contract_compliance=[],
            cross_worker_issues=["前端调用 /auth/login，后端实现 /api/auth/login"],
            action="challenge",
            challenge_targets=[
                ChallengeTarget(
                    worker_id="backend-1",
                    challenge_message="API 路径应该是 /api/auth/login",
                    affected_files=["src/api/auth.py"],
                )
            ],
            replan_reason=None,
            stage_summary="部分完成",
        )
        d = rv.to_dict()
        assert d["action"] == "challenge"
        assert len(d["challenge_targets"]) == 1
        assert d["challenge_targets"][0]["worker_id"] == "backend-1"
        rv2 = ReviewVerdict.from_dict(d)
        assert rv2.action == "challenge"
        assert rv2.challenge_targets[0].worker_id == "backend-1"

    def test_review_verdict_replan(self):
        rv = ReviewVerdict(
            verdict="BLOCK",
            checklist=[],
            quality_notes=[],
            risks=[],
            suggestions=[],
            contract_compliance=[],
            cross_worker_issues=[],
            action="replan",
            challenge_targets=None,
            replan_reason="发现新的依赖关系，需要重新规划",
            stage_summary="",
        )
        d = rv.to_dict()
        assert d["action"] == "replan"
        assert d["replan_reason"] == "发现新的依赖关系，需要重新规划"
        rv2 = ReviewVerdict.from_dict(d)
        assert rv2.action == "replan"


class TestPlanAdversaryVerdict:
    def test_plan_adversary_verdict_pass(self):
        pv = PlanAdversaryVerdict(
            verdict="pass",
            issues=[],
            auto_fixes=[],
            summary="计划质量可接受",
        )
        d = pv.to_dict()
        assert d["verdict"] == "pass"
        assert d["summary"] == "计划质量可接受"
        pv2 = PlanAdversaryVerdict.from_dict(d)
        assert pv2.verdict == "pass"

    def test_plan_adversary_verdict_fix(self):
        pv = PlanAdversaryVerdict(
            verdict="fix",
            issues=[
                PlanIssue(
                    category="contract",
                    severity="warn",
                    location="stages[0].contract.api_endpoints[0]",
                    description="缺少 response_body",
                    suggestion="添加 response_body: {\"token\": \"str\"}",
                )
            ],
            auto_fixes=[
                AutoFix(
                    location="stages[0].contract.api_endpoints[0].response_body",
                    current_value=None,
                    suggested_value={"token": "str"},
                    reason="API 定义需要明确响应格式",
                )
            ],
            summary="发现 1 个可自动修复的问题",
        )
        d = pv.to_dict()
        assert d["verdict"] == "fix"
        assert len(d["issues"]) == 1
        assert d["issues"][0]["category"] == "contract"
        assert len(d["auto_fixes"]) == 1
        pv2 = PlanAdversaryVerdict.from_dict(d)
        assert pv2.verdict == "fix"
        assert pv2.issues[0].severity == "warn"

    def test_plan_adversary_verdict_reject(self):
        pv = PlanAdversaryVerdict(
            verdict="reject",
            issues=[
                PlanIssue(
                    category="scope",
                    severity="block",
                    location="stages",
                    description="stages 没有覆盖前端需求",
                    suggestion="增加前端阶段",
                )
            ],
            auto_fixes=[],
            summary="计划有严重缺陷",
        )
        d = pv.to_dict()
        assert d["verdict"] == "reject"
        assert d["issues"][0]["severity"] == "block"
        pv2 = PlanAdversaryVerdict.from_dict(d)
        assert pv2.verdict == "reject"


class TestChallengeTarget:
    def test_challenge_target_to_dict_roundtrip(self):
        ct = ChallengeTarget(
            worker_id="backend-1",
            challenge_message="缺少 rate limiting",
            affected_files=["src/api/auth.py"],
        )
        d = ct.to_dict()
        assert d["worker_id"] == "backend-1"
        assert d["affected_files"] == ["src/api/auth.py"]
        ct2 = ChallengeTarget.from_dict(d)
        assert ct2.worker_id == "backend-1"


class TestCheckItem:
    def test_check_item_to_dict_roundtrip(self):
        ci = CheckItem(criterion="RS256", status="pass", note="已实现")
        d = ci.to_dict()
        assert d["criterion"] == "RS256"
        assert d["status"] == "pass"
        ci2 = CheckItem.from_dict(d)
        assert ci2.criterion == "RS256"

    def test_check_item_defaults(self):
        ci = CheckItem(criterion="X", status="pass")
        assert ci.note == ""


# --- Default-value tests ---


class TestDataModelDefaults:
    def test_data_model_default_fields(self):
        dm = DataModel(name="X")
        assert dm.fields == {}


class TestStagePlanDefaults:
    def test_stage_plan_default_dependencies(self):
        sp = StagePlan(
            stage_id=1,
            goal="test",
            acceptance_criteria=["ac1"],
            contract=Contract(),
            sub_tasks=[],
        )
        assert sp.dependencies == []


class TestThinkResultDefaults:
    def test_think_result_default_open_questions(self):
        tr = ThinkResult(
            spec="test",
            stages=[],
            contract=Contract(),
            project_context=ProjectContext(),
            acceptance_criteria=["ac1"],
        )
        assert tr.open_questions == []


class TestReviewVerdictDefaults:
    def test_review_verdict_default_optional_fields(self):
        rv = ReviewVerdict(
            verdict="OK",
            checklist=[],
            quality_notes=[],
            risks=[],
            suggestions=[],
            contract_compliance=[],
            cross_worker_issues=[],
            action="pass",
        )
        assert rv.challenge_targets is None
        assert rv.replan_reason is None
        assert rv.stage_summary == ""


class TestChallengeTargetDefaults:
    def test_challenge_target_default_affected_files(self):
        ct = ChallengeTarget(worker_id="X", challenge_message="Y")
        assert ct.affected_files == []


class TestPlanAdversaryVerdictDefaults:
    def test_plan_adversary_verdict_default_summary(self):
        pv = PlanAdversaryVerdict(verdict="pass", issues=[], auto_fixes=[])
        assert pv.summary == ""


class TestPlanIssueDefaults:
    def test_plan_issue_default_suggestion(self):
        pi = PlanIssue(category="X", severity="Y", location="Z", description="W")
        assert pi.suggestion == ""


class TestAutoFixDefaults:
    def test_auto_fix_default_reason(self):
        af = AutoFix(location="X", current_value=None, suggested_value=None)
        assert af.reason == ""


# --- Edge-case tests ---


class TestReviewVerdictEdgeCases:
    def test_from_dict_challenge_targets_absent(self):
        """When 'challenge_targets' key is missing, the field should be None."""
        d = {
            "verdict": "OK",
            "checklist": [],
            "quality_notes": [],
            "risks": [],
            "suggestions": [],
            "contract_compliance": [],
            "cross_worker_issues": [],
            "action": "pass",
        }
        rv = ReviewVerdict.from_dict(d)
        assert rv.challenge_targets is None

    def test_from_dict_challenge_targets_none(self):
        """When 'challenge_targets' key is present but None, the field should be None."""
        d = {
            "verdict": "OK",
            "checklist": [],
            "quality_notes": [],
            "risks": [],
            "suggestions": [],
            "contract_compliance": [],
            "cross_worker_issues": [],
            "action": "pass",
            "challenge_targets": None,
        }
        rv = ReviewVerdict.from_dict(d)
        assert rv.challenge_targets is None

    def test_from_dict_challenge_targets_empty_list(self):
        """When 'challenge_targets' is an empty list, the field should be []."""
        d = {
            "verdict": "OK",
            "checklist": [],
            "quality_notes": [],
            "risks": [],
            "suggestions": [],
            "contract_compliance": [],
            "cross_worker_issues": [],
            "action": "pass",
            "challenge_targets": [],
        }
        rv = ReviewVerdict.from_dict(d)
        assert rv.challenge_targets == []


class TestFromDictEmptyLists:
    def test_contract_from_dict_empty_lists(self):
        d = {
            "api_endpoints": [],
            "data_models": [],
            "shared_types": [],
            "conventions": [],
        }
        c = Contract.from_dict(d)
        assert c.api_endpoints == []
        assert c.data_models == []
        assert c.shared_types == []
        assert c.conventions == []

    def test_stage_plan_from_dict_empty_subtasks(self):
        d = {
            "stage_id": 1,
            "goal": "test",
            "acceptance_criteria": [],
            "contract": {"api_endpoints": [], "data_models": [], "shared_types": [], "conventions": []},
            "sub_tasks": [],
            "dependencies": [],
        }
        sp = StagePlan.from_dict(d)
        assert sp.sub_tasks == []
        assert sp.dependencies == []
        assert sp.acceptance_criteria == []

    def test_think_result_from_dict_empty_lists(self):
        d = {
            "spec": "test",
            "stages": [],
            "contract": {"api_endpoints": [], "data_models": [], "shared_types": [], "conventions": []},
            "project_context": {"structure": "", "existing_patterns": [], "tech_stack": [], "related_files": [], "constraints": []},
            "acceptance_criteria": [],
            "open_questions": [],
        }
        tr = ThinkResult.from_dict(d)
        assert tr.stages == []
        assert tr.open_questions == []
        assert tr.acceptance_criteria == []

    def test_plan_adversary_verdict_from_dict_empty_lists(self):
        d = {"verdict": "pass", "issues": [], "auto_fixes": []}
        pv = PlanAdversaryVerdict.from_dict(d)
        assert pv.issues == []
        assert pv.auto_fixes == []

    def test_challenge_target_from_dict_empty_affected_files(self):
        d = {"worker_id": "w1", "challenge_message": "msg", "affected_files": []}
        ct = ChallengeTarget.from_dict(d)
        assert ct.affected_files == []
