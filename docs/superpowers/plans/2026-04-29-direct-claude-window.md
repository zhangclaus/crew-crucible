# Direct Claude Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `orchestrator claude open`, a direct Terminal launcher for interactive Claude CLI sessions supervised by Codex.

**Architecture:** Add a focused `claude_window.py` module for prompt/script generation and macOS Terminal launching. Wire a new `claude open` CLI branch that calls the launcher and returns JSON. Keep tmux and V2 session paths unchanged.

**Tech Stack:** Python stdlib (`argparse`, `json`, `pathlib`, `shlex`, `subprocess`, `uuid`), macOS `osascript`, `pbcopy`, `script`, Claude CLI, pytest.

---

## Tasks

- [x] Add failing tests for prompt/script generation and Terminal launch command.
- [x] Implement `ClaudeWindowLauncher` and `ClaudeWindowLaunch`.
- [x] Add failing CLI tests for `claude open`.
- [x] Wire `claude open` into `cli.py`.
- [x] Run targeted tests and full test suite.
- [x] Commit docs and implementation.
