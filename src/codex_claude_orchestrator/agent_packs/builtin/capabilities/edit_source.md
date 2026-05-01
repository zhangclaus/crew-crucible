## Capability: edit_source

Allowed actions:
- Modify source files inside the contract write scope.
- Keep patches narrow and explain changed files.

Forbidden actions:
- Do not edit orchestrator control state.
- Do not silently expand scope beyond the contract.

Required report:
- changed_files
- implementation_summary
- verification_notes
