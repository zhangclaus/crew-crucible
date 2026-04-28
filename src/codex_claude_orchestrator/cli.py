import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dispatch = subparsers.add_parser("dispatch", help="Dispatch a task to a worker")
    dispatch.add_argument("--task-id", required=False)
    dispatch.add_argument("--goal", required=True)
    dispatch.add_argument("--repo", required=True)
    dispatch.add_argument(
        "--workspace-mode",
        choices=("isolated", "shared", "readonly"),
        default="isolated",
    )
    dispatch.add_argument(
        "--allow-shared-write",
        action="store_true",
        help="Allow a worker to write directly in shared workspace mode",
    )
    dispatch.add_argument("--assigned-agent", default="claude")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
