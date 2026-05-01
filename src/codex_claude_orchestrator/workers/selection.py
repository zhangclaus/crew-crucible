from __future__ import annotations

from dataclasses import dataclass

from codex_claude_orchestrator.crew.models import WorkerRole


@dataclass(frozen=True, slots=True)
class WorkerSelection:
    roles: list[WorkerRole]
    mode: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "selected_workers": [role.value for role in self.roles],
            "selection_mode": self.mode,
            "selection_reason": self.reason,
        }


class WorkerSelectionPolicy:
    def select(self, *, goal: str, workers: str | None = "auto", mode: str = "auto") -> WorkerSelection:
        workers = (workers or "auto").strip()
        mode = (mode or "auto").strip()
        if workers != "auto":
            return WorkerSelection(
                roles=self._parse_roles(workers),
                mode="explicit",
                reason="explicit --workers override",
            )
        if mode != "auto":
            return WorkerSelection(
                roles=self._roles_for_mode(mode),
                mode=mode,
                reason=f"explicit --mode {mode}",
            )
        inferred_mode, reason = self._infer_mode(goal)
        return WorkerSelection(roles=self._roles_for_mode(inferred_mode), mode=inferred_mode, reason=reason)

    def _parse_roles(self, value: str) -> list[WorkerRole]:
        roles = [WorkerRole(item.strip()) for item in value.split(",") if item.strip()]
        if not roles:
            raise ValueError("at least one worker role is required")
        return roles

    def _roles_for_mode(self, mode: str) -> list[WorkerRole]:
        if mode == "quick":
            return [WorkerRole.IMPLEMENTER]
        if mode == "standard":
            return [WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER]
        if mode == "full":
            return [WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER, WorkerRole.REVIEWER]
        raise ValueError(f"unsupported worker selection mode: {mode}")

    def _infer_mode(self, goal: str) -> tuple[str, str]:
        normalized = goal.lower()
        if self._contains_any(
            normalized,
            [
                "typo",
                "readme",
                "拼写",
                "错别字",
                "文档",
                "注释",
                "格式",
                "小改",
                "单点",
            ],
        ):
            return "quick", "small, localized change can use implementer only"
        if self._contains_any(
            normalized,
            [
                "review",
                "risk",
                "risky",
                "refactor",
                "architecture",
                "重构",
                "架构",
                "审查",
                "评审",
                "检查",
                "完善",
                "多文件",
                "高风险",
                "llm-wiki",
            ],
        ):
            return "full", "goal benefits from exploration and independent review"
        return "standard", "default to exploration plus implementation"

    def _contains_any(self, value: str, keywords: list[str]) -> bool:
        return any(keyword in value for keyword in keywords)
