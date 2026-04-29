from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.models import LearningNote, SkillRecord, SkillStatus, utc_now


class SkillSecurityError(ValueError):
    pass


class SkillEvolution:
    _UNSAFE_PATTERNS = (
        re.compile(r"\b[A-Z0-9_]*API_KEY\s*=", re.IGNORECASE),
        re.compile(r"BEGIN [A-Z ]*PRIVATE KEY", re.IGNORECASE),
        re.compile(r"\bbypass\s+policy\s+gates?\b", re.IGNORECASE),
        re.compile(r"\bignore\s+(?:approval|policy)\s+checks?\b", re.IGNORECASE),
    )

    def __init__(self, state_root: Path):
        self._state_root = state_root
        self._skills_root = state_root / "skills"
        self._index_path = self._skills_root / "index.json"

    def create_pending_skill(
        self,
        learning_note: LearningNote,
        *,
        procedure: list[str] | None = None,
        pitfalls: list[str] | None = None,
        verification: list[str] | None = None,
    ) -> SkillRecord:
        name = self._sanitize_name(learning_note.proposed_skill_name or learning_note.summary)
        skill_dir = self._skill_dir(SkillStatus.PENDING, name)
        if skill_dir.exists():
            raise FileExistsError(f"skill already exists: {name}")

        procedure_items = procedure or [learning_note.summary]
        pitfalls_items = pitfalls or ["Keep the skill narrowly tied to the source evidence."]
        verification_items = verification or ["Run the relevant verification command before relying on this skill."]
        self._scan_content(
            [
                learning_note.summary,
                learning_note.evidence_summary,
                *learning_note.trigger_conditions,
                *procedure_items,
                *pitfalls_items,
                *verification_items,
            ]
        )

        skill_md = self._render_skill_md(
            learning_note=learning_note,
            procedure=procedure_items,
            pitfalls=pitfalls_items,
            verification=verification_items,
        )
        record = SkillRecord(
            skill_id=f"skill-{uuid.uuid4().hex}",
            name=name,
            status=SkillStatus.PENDING,
            source_session_id=learning_note.session_id,
            learning_note_id=learning_note.note_id,
            path=skill_dir / "SKILL.md",
            trigger_conditions=list(learning_note.trigger_conditions),
            validation_summary="pending human approval",
            summary=learning_note.summary,
        )
        evidence = {
            "learning_note": learning_note.to_dict(),
            "source_challenge_ids": list(learning_note.challenge_ids),
        }

        skill_dir.mkdir(parents=True, exist_ok=False)
        self._write_text(skill_dir / "SKILL.md", skill_md)
        self._write_json(skill_dir / "metadata.json", record.to_dict())
        self._write_json(skill_dir / "evidence.json", evidence)
        self._upsert_index(record)
        return record

    def approve_skill(self, name: str) -> SkillRecord:
        return self._move_skill(name, SkillStatus.PENDING, SkillStatus.ACTIVE)

    def reject_skill(self, name: str, reason: str = "") -> SkillRecord:
        return self._move_skill(name, SkillStatus.PENDING, SkillStatus.REJECTED, rejection_reason=reason)

    def list_skills(self, status: SkillStatus | None = None) -> list[dict[str, Any]]:
        index = self._read_index()
        skills = list(index.values())
        if status is not None:
            skills = [skill for skill in skills if skill["status"] == status.value]
        return sorted(skills, key=lambda skill: (skill.get("updated_at", ""), skill["name"]), reverse=True)

    def show_skill(self, name: str) -> dict[str, Any]:
        sanitized = self._sanitize_name(name)
        index = self._read_index()
        if sanitized not in index:
            raise FileNotFoundError(f"skill not found: {sanitized}")

        entry = index[sanitized]
        skill_dir = Path(entry["path"]).parent
        return {
            "record": entry,
            "skill": (skill_dir / "SKILL.md").read_text(encoding="utf-8"),
            "metadata": self._read_json(skill_dir / "metadata.json"),
            "evidence": self._read_json(skill_dir / "evidence.json"),
        }

    def _move_skill(
        self,
        name: str,
        from_status: SkillStatus,
        to_status: SkillStatus,
        *,
        rejection_reason: str = "",
    ) -> SkillRecord:
        sanitized = self._sanitize_name(name)
        source_dir = self._skill_dir(from_status, sanitized)
        target_dir = self._skill_dir(to_status, sanitized)
        if not source_dir.is_dir():
            raise FileNotFoundError(f"{from_status.value} skill not found: {sanitized}")
        if target_dir.exists():
            raise FileExistsError(f"{to_status.value} skill already exists: {sanitized}")

        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(target_dir))

        metadata_path = target_dir / "metadata.json"
        metadata = self._read_json(metadata_path)
        metadata["status"] = to_status.value
        metadata["path"] = str(target_dir / "SKILL.md")
        metadata["updated_at"] = utc_now()
        if rejection_reason:
            metadata["rejection_reason"] = rejection_reason
        self._write_json(metadata_path, metadata)

        record = self._record_from_metadata(metadata)
        self._upsert_index(record, extra={"rejection_reason": rejection_reason} if rejection_reason else None)
        return record

    def _upsert_index(self, record: SkillRecord, extra: dict[str, Any] | None = None) -> None:
        index = self._read_index()
        entry = record.to_dict()
        if extra:
            entry.update(extra)
        index[record.name] = entry
        self._write_json(self._index_path, index)

    def _record_from_metadata(self, metadata: dict[str, Any]) -> SkillRecord:
        return SkillRecord(
            skill_id=metadata["skill_id"],
            name=metadata["name"],
            status=SkillStatus(metadata["status"]),
            source_session_id=metadata["source_session_id"],
            learning_note_id=metadata["learning_note_id"],
            path=Path(metadata["path"]),
            version=metadata.get("version", "0.1.0"),
            trigger_conditions=list(metadata.get("trigger_conditions", [])),
            validation_summary=metadata.get("validation_summary", ""),
            approval_mode=metadata.get("approval_mode", "human"),
            summary=metadata.get("summary", ""),
            created_at=metadata.get("created_at", utc_now()),
            updated_at=metadata.get("updated_at", utc_now()),
        )

    def _render_skill_md(
        self,
        *,
        learning_note: LearningNote,
        procedure: list[str],
        pitfalls: list[str],
        verification: list[str],
    ) -> str:
        return "\n".join(
            [
                f"# {learning_note.proposed_skill_name or learning_note.summary}",
                "",
                "## When to Use",
                *self._bullets(learning_note.trigger_conditions or [learning_note.summary]),
                "",
                "## Procedure",
                *self._numbered(procedure),
                "",
                "## Pitfalls",
                *self._bullets(pitfalls),
                "",
                "## Verification",
                *self._bullets(verification),
                "",
                "## Source Evidence",
                f"- Session: {learning_note.session_id}",
                f"- Learning note: {learning_note.note_id}",
                f"- Challenges: {', '.join(learning_note.challenge_ids) if learning_note.challenge_ids else 'none'}",
                f"- Evidence: {learning_note.evidence_summary or learning_note.summary}",
                "",
            ]
        )

    def _scan_content(self, values: list[str]) -> None:
        content = "\n".join(values)
        for pattern in self._UNSAFE_PATTERNS:
            if pattern.search(content):
                raise SkillSecurityError("skill content failed security scan")

    def _sanitize_name(self, value: str) -> str:
        name = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        name = re.sub(r"-+", "-", name)
        if not name:
            raise ValueError("skill name must contain letters or digits")
        return name

    def _skill_dir(self, status: SkillStatus, name: str) -> Path:
        return self._skills_root / status.value / name

    def _read_index(self) -> dict[str, dict[str, Any]]:
        if not self._index_path.exists():
            return {}
        return self._read_json(self._index_path)

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)

    def _bullets(self, items: list[str]) -> list[str]:
        return [f"- {item}" for item in items]

    def _numbered(self, items: list[str]) -> list[str]:
        return [f"{index}. {item}" for index, item in enumerate(items, start=1)]
