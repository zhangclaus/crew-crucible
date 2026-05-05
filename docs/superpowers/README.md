# Superpowers Design Docs

This directory keeps the design history for the Codex-managed Claude orchestrator.

## Current Reference Docs

- `specs/2026-04-30-dynamic-agent-orchestrator-current-architecture.zh.md`
  - Current architecture and pipeline review document.
  - Start here when evaluating whether the dynamic crew design is reasonable.
- `specs/2026-04-30-dynamic-agent-role-pack-design.zh.md`
  - Original dynamic worker contract design spec.
- `plans/2026-04-29-codex-managed-claude-crew-v3.zh.md`
  - Main V3 implementation plan lineage.

## Historical Docs

Older dated files remain in place because previous discussions and implementation notes refer to their exact paths. Treat them as design history unless a current reference doc points back to them.

## Organization Rule

New architecture summaries should go in `specs/` with a date and a precise topic. New implementation plans should go in `plans/`. If a future cleanup archives old documents, preserve redirect notes so existing references do not go stale.
