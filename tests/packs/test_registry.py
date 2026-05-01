from codex_claude_orchestrator.packs.registry import AgentPackRegistry
from codex_claude_orchestrator.crew.models import (
    AgentProfile,
    AuthorityLevel,
    WorkerContract,
    WorkspacePolicy,
)


def test_builtin_agent_pack_registry_lists_and_loads_capability_and_protocol_fragments():
    registry = AgentPackRegistry.builtin()

    assert "inspect_code" in registry.list_capabilities()
    assert "review_patch" in registry.list_capabilities()
    assert "research_external" in registry.list_capabilities()
    assert "write_docs" in registry.list_capabilities()
    assert "design_architecture" in registry.list_capabilities()
    assert "task_confirmation" in registry.list_protocols()
    assert "three_strike_escalation" in registry.list_protocols()
    assert "Required report" in registry.capability_fragment("inspect_code")
    assert "OK/WARN/BLOCK" in registry.protocol_fragment("review_dimensions")


def test_agent_profile_renders_capability_and_protocol_pack_fragments():
    registry = AgentPackRegistry.builtin()
    contract = WorkerContract(
        contract_id="contract-review",
        label="patch-risk-auditor",
        mission="Review the patch.",
        required_capabilities=["review_patch", "inspect_code"],
        authority_level=AuthorityLevel.READONLY,
        workspace_policy=WorkspacePolicy.READONLY,
        protocol_refs=["review_dimensions"],
    )

    profile = AgentProfile(
        profile_id="profile-review",
        contract=contract,
        capability_fragments=registry.capability_fragments_for(contract.required_capabilities),
        protocol_packs=registry.protocol_fragments_for(contract.protocol_refs),
    )
    prompt = profile.render_prompt()

    assert "## Capability: review_patch" in prompt
    assert "## Capability: inspect_code" in prompt
    assert "## Protocol: review_dimensions" in prompt
    assert "OK/WARN/BLOCK" in prompt
