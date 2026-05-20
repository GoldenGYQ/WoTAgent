"""WoTAgent CLI — subcommands for repl, log, and api.

Usage:
    wotagent repl              Interactive chat REPL
    wotagent log               Tail the wotagent.log file
    wotagent api               Start the FastAPI + SSE server
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project src is on the path when run as `python -m wotagent.cli`
_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path, encoding="utf-8")


def _run_repl(args: argparse.Namespace) -> None:
    """Launch the interactive chat REPL."""
    import asyncio

    # CLI flags override env; None = read from env
    enable_thinking = args.thinking  # None, True, or False
    enable_streaming = args.streaming  # None, True, or False

    from wotagent.repl_impl import run_repl
    asyncio.run(run_repl(
        log_mode="file",
        enable_thinking=enable_thinking,
        enable_streaming=enable_streaming,
    ))


def _run_log(args: argparse.Namespace) -> None:
    """Tail the WoTAgent log file to stdout."""
    import asyncio
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Default to project-root wotagent.log
    log_path = args.file
    if log_path == "wotagent.log":
        log_path = str(Path(__file__).resolve().parents[2] / "wotagent.log")

    from wotagent.log_viewer import run_log_viewer
    asyncio.run(run_log_viewer(log_file=log_path, follow=not args.no_follow))


def _run_api(args: argparse.Namespace) -> None:
    """Start the FastAPI server."""
    import uvicorn
    from wotagent.logging import configure_logging

    configure_logging(mode="console")
    print(f"  WoTAgent API → http://{args.host}:{args.port}")
    print(f"  SSE events   → http://{args.host}:{args.port}/api/events/{{session_id}}")
    uvicorn.run(
        "wotagent.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wotagent",
        description="WoTAgent — Event-driven IoT Agent with LangChain",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- repl ---
    repl_parser = sub.add_parser("repl", help="Interactive chat REPL (logs go to wotagent.log)")
    repl_parser.add_argument(
        "--thinking", action="store_true", default=None,
        help="Enable DeepSeek thinking mode (overrides ENABLE_THINKING env)",
    )
    repl_parser.add_argument(
        "--no-thinking", action="store_false", dest="thinking", default=None,
        help="Disable DeepSeek thinking mode",
    )
    repl_parser.add_argument(
        "--streaming", action="store_true", default=None,
        help="Enable streaming output (overrides ENABLE_STREAMING env)",
    )
    repl_parser.add_argument(
        "--no-streaming", action="store_false", dest="streaming", default=None,
        help="Disable streaming output",
    )

    # --- log ---
    log_parser = sub.add_parser("log", help="Tail wotagent.log")
    log_parser.add_argument(
        "-f", "--file", default="wotagent.log",
        help="Log file path (default: wotagent.log)",
    )
    log_parser.add_argument(
        "--no-follow", action="store_true",
        help="Print current log contents and exit (don't tail)",
    )

    # --- api ---
    api_parser = sub.add_parser("api", help="Start the FastAPI + SSE server")
    api_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    api_parser.add_argument("--port", type=int, default=8000, help="Bind port")
    api_parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")

    args = parser.parse_args()

    if args.command == "repl":
        _run_repl(args)
    elif args.command == "log":
        _run_log(args)
    elif args.command == "api":
        _run_api(args)


if __name__ == "__main__":
    main()
