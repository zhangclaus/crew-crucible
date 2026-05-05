from pathlib import Path

from codex_claude_orchestrator.v4.repo_intelligence import RepoIntelligence


def test_repo_intelligence_infers_scope_risks_and_verification(tmp_path: Path) -> None:
    (tmp_path / "src/api").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")

    report = RepoIntelligence().analyze(
        repo_root=tmp_path,
        goal="Change public API behavior",
        changed_files=[
            "src/api/routes.py",
            "tests/test_routes.py",
            "docs/api.md",
            "pyproject.toml",
        ],
    )

    assert report.write_scope == ["src/", "tests/", "docs/"]
    assert report.package_boundaries == ["docs", "src", "tests"]
    assert {"public_api", "tests", "docs", "config"}.issubset(set(report.risk_tags))
    assert report.suggested_verification_commands == ["pytest -q"]
