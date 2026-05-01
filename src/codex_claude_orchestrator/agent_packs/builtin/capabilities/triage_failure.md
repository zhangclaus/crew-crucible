## Capability: triage_failure

Allowed actions:
- Classify repeated verification failures.
- Identify smallest repair path or escalation path.

Forbidden actions:
- Do not retry blindly without new evidence.

Required report:
- failure_class
- root_cause_hypothesis
- repair_instruction
- escalation_recommendation
