from __future__ import annotations

from pathlib import Path


class AgentPackRegistry:
    def __init__(self, root: Path):
        self._root = root
        self._capabilities_dir = root / "capabilities"
        self._protocols_dir = root / "protocols"

    @classmethod
    def builtin(cls) -> "AgentPackRegistry":
        return cls(Path(__file__).parents[1] / "agent_packs" / "builtin")

    def list_capabilities(self) -> list[str]:
        return self._list_markdown_names(self._capabilities_dir)

    def list_protocols(self) -> list[str]:
        return self._list_markdown_names(self._protocols_dir)

    def capability_fragment(self, capability: str) -> str:
        return self._read_fragment(self._capabilities_dir, capability, "capability")

    def protocol_fragment(self, protocol: str) -> str:
        return self._read_fragment(self._protocols_dir, protocol, "protocol")

    def capability_fragments_for(self, capabilities: list[str]) -> list[str]:
        return [self.capability_fragment(capability) for capability in capabilities if self._has_fragment(self._capabilities_dir, capability)]

    def protocol_fragments_for(self, protocols: list[str]) -> list[str]:
        return [self.protocol_fragment(protocol) for protocol in protocols if self._has_fragment(self._protocols_dir, protocol)]

    def _list_markdown_names(self, directory: Path) -> list[str]:
        if not directory.exists():
            return []
        return sorted(path.stem for path in directory.glob("*.md") if path.is_file())

    def _read_fragment(self, directory: Path, name: str, kind: str) -> str:
        path = directory / f"{name}.md"
        if not path.exists():
            raise KeyError(f"unknown {kind}: {name}")
        return path.read_text(encoding="utf-8").strip()

    def _has_fragment(self, directory: Path, name: str) -> bool:
        return (directory / f"{name}.md").exists()
