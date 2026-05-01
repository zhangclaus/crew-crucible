## Capability: run_verification

Allowed actions:
- Run verification commands authorized by Codex.
- Summarize command, cwd, exit code, and failure excerpt.

Forbidden actions:
- Do not treat manual inspection as verification evidence.

Required report:
- command
- passed
- summary
- artifact_refs
