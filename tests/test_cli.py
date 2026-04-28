from codex_claude_orchestrator.cli import build_parser


def test_build_parser_exposes_dispatch_subcommand():
    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")
    assert "dispatch" in subparsers_action.choices
