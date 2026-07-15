"""WoTAgent 2.0 command line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path, encoding="utf-8")

_ENV_TEMPLATE = """# WoTAgent 2.0
LLM_MODEL={model}
OPENAI_BASE_URL={base_url}
OPENAI_API_KEY={api_key}
ENABLE_THINKING={thinking}
ENABLE_STREAMING=true
MCP_TRANSPORT_MODE=stdio
"""


def _prompt(label: str, default: str = "", sensitive: bool = False) -> str:
    prompt_text = f"{label} [{default}]: " if default else f"{label}: "
    if sensitive:
        import getpass

        value = getpass.getpass(prompt_text)
    else:
        value = input(prompt_text).strip()
    return value or default


def _setup_env() -> None:
    if _env_path.exists():
        return
    print("WoTAgent 2.0 first-time setup")
    content = _ENV_TEMPLATE.format(
        api_key=_prompt("API key", sensitive=True) or "your-api-key-here",
        base_url=_prompt("API base URL", "https://api.deepseek.com"),
        model=_prompt("Model", "deepseek-chat"),
        thinking=_prompt("Enable thinking (true/false)", "true"),
    )
    _env_path.write_text(content, encoding="utf-8")
    load_dotenv(_env_path, encoding="utf-8")


def _run_log(args: argparse.Namespace) -> None:
    _setup_env()
    import asyncio

    from wotagent.cli.log import run_log_viewer

    path = args.file
    if path == "wotagent.log":
        path = str(Path(__file__).resolve().parents[3] / path)
    asyncio.run(run_log_viewer(log_file=path, follow=not args.no_follow))


def _run_api(args: argparse.Namespace) -> None:
    _setup_env()
    import uvicorn

    print(f"WoTAgent API -> http://{args.host}:{args.port}")
    print(f"Trace WS    -> ws://{args.host}:{args.port}/ws/v2")
    uvicorn.run(
        "wotagent.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="wotagent", description="WoTAgent 2.0")
    sub = parser.add_subparsers(dest="command", required=True)

    log_parser = sub.add_parser("log", help="Tail wotagent.log")
    log_parser.add_argument("-f", "--file", default="wotagent.log")
    log_parser.add_argument("--no-follow", action="store_true")

    api_parser = sub.add_parser("api", help="Start the FastAPI service")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", type=int, default=8000)
    api_parser.add_argument("--reload", action="store_true")

    args = parser.parse_args()
    if args.command == "log":
        _run_log(args)
    elif args.command == "api":
        _run_api(args)
