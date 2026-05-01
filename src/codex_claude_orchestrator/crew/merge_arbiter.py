from __future__ import annotations


class MergeArbiter:
    def build_plan(self, crew_id: str, *, changed_files_by_worker: dict[str, list[str]]) -> dict:
        path_owners: dict[str, list[str]] = {}
        for worker_id, paths in changed_files_by_worker.items():
            for path in paths:
                path_owners.setdefault(path, []).append(worker_id)

        conflicts = [
            {"path": path, "workers": owners}
            for path, owners in sorted(path_owners.items())
            if len(set(owners)) > 1
        ]
        can_merge = not conflicts
        return {
            "crew_id": crew_id,
            "can_merge": can_merge,
            "conflicts": conflicts,
            "changed_files_by_worker": changed_files_by_worker,
            "recommendation": "ready_for_codex_review" if can_merge else "requires_codex_decision",
        }
