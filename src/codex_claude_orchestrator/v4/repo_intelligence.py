"""Lightweight repository intelligence for V4 planning decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RepoIntelligenceReport:
    write_scope: list[str] = field(default_factory=list)
    package_boundaries: list[str] = field(default_factory=list)
    risk_tags: list[str] = field(default_factory=list)
    suggested_verification_commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "write_scope": self.write_scope,
            "package_boundaries": self.package_boundaries,
            "risk_tags": self.risk_tags,
            "suggested_verification_commands": self.suggested_verification_commands,
        }


class RepoIntelligence:
    def analyze(
        self,
        *,
        repo_root: Path,
        goal: str = "",
        changed_files: list[str] | None = None,
    ) -> RepoIntelligenceReport:
        changed_files = [path for path in (changed_files or []) if isinstance(path, str) and path]
        package_boundaries = _package_boundaries(changed_files)
        write_scope = _write_scope(repo_root, package_boundaries)
        risk_tags = _risk_tags(goal=goal, changed_files=changed_files)
        return RepoIntelligenceReport(
            write_scope=write_scope,
            package_boundaries=package_boundaries,
            risk_tags=risk_tags,
            suggested_verification_commands=_verification_commands(repo_root, risk_tags),
        )


def _package_boundaries(changed_files: list[str]) -> list[str]:
    boundaries = []
    for file_path in changed_files:
        parts = Path(file_path).parts
        if len(parts) > 1:
            boundaries.append(parts[0])
    return sorted(set(boundaries))


def _write_scope(repo_root: Path, package_boundaries: list[str]) -> list[str]:
    if package_boundaries:
        scopes = [
            f"{boundary}/"
            for boundary in _preferred_order(package_boundaries)
            if (repo_root / boundary).is_dir()
        ]
        if scopes:
            return scopes
    roots = [
        f"{name}/"
        for name in ("src", "tests", "test", "tools", "packages", "apps", "app", "lib", "scripts", "docs")
        if (repo_root / name).is_dir()
    ]
    return roots or ["src/", "tests/"]


def _preferred_order(values: list[str]) -> list[str]:
    preferred = ["src", "tests", "test", "tools", "packages", "apps", "app", "lib", "scripts", "docs"]
    ordered = [value for value in preferred if value in values]
    ordered.extend(sorted(value for value in values if value not in preferred))
    return ordered


def _risk_tags(*, goal: str, changed_files: list[str]) -> list[str]:
    tags: list[str] = []
    normalized_goal = goal.lower()
    if any(keyword in normalized_goal for keyword in ("api", "public", "公开", "接口")):
        tags.append("public_api")
    for file_path in changed_files:
        path = Path(file_path)
        parts = [part.lower() for part in path.parts]
        suffix = path.suffix.lower()
        name = path.name.lower()
        if parts and parts[0] in {"tests", "test"} or name.startswith("test_"):
            tags.append("tests")
        if parts and parts[0] == "docs" or suffix in {".md", ".mdx", ".rst"}:
            tags.append("docs")
        if "api" in parts or name in {"__init__.py", "cli.py"}:
            tags.append("public_api")
        if suffix in {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg"} or name in {
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
        }:
            tags.append("config")
        if suffix in {".tsx", ".jsx", ".vue", ".svelte"} or any(part in {"web", "frontend", "ui"} for part in parts):
            tags.append("frontend")
        if any(part in {"migration", "migrations"} for part in parts):
            tags.append("migration")
        if any(part in {"generated", "gen"} for part in parts):
            tags.append("generated")
        if suffix in {".sql"} or any(part in {"db", "database"} for part in parts):
            tags.append("database")
        if any(part in {"auth", "security", "permissions"} for part in parts):
            tags.append("security")
    return sorted(set(tags))


def _verification_commands(repo_root: Path, risk_tags: list[str]) -> list[str]:
    commands: list[str] = []
    if (
        (repo_root / "pyproject.toml").exists()
        or (repo_root / "pytest.ini").exists()
        or (repo_root / "tests").is_dir()
    ):
        commands.append("pytest -q")
    if "frontend" in risk_tags and (repo_root / "package.json").exists():
        commands.append("npm test")
    return commands


__all__ = ["RepoIntelligence", "RepoIntelligenceReport"]
