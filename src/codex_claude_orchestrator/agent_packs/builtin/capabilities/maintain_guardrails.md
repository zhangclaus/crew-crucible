## Capability: maintain_guardrails

Allowed actions:
- Convert repeated failures into guardrail notes or focused checks.
- Preserve evidence references for future reviewers.

Forbidden actions:
- Do not add broad policy unrelated to the observed failure.

Required report:
- known_pitfall
- guardrail
- evidence_refs
- proposed_check
