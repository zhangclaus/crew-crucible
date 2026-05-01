## Capability: review_patch

Allowed actions:
- Review changed files, diff artifacts, and test evidence.
- Report correctness, regression, scope, and maintainability findings.

Forbidden actions:
- Do not rewrite the patch unless the contract also grants write authority.

Required report:
- verdict
- findings
- evidence_refs
- risk_summary
