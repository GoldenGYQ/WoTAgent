"""WoTAgent REPL — interactive chat frontend for the event-driven agent.

Exposes ``run_repl()`` for use by both ``repl.py`` (root wrapper) and
``wotagent cli`` (installed CLI entry point).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from wotagent.core import create_wot_pipeline, get_session_manager, invoke_agent_stream
from wotagent.logging import configure_logging, install_event_logger
from wotagent.perception import get_perception_engine
from wotagent.wot import load_tds


def _bool_env(key: str, default: bool = True) -> bool:
    """Read a boolean from an env var, or fall back to *default*."""
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def print_header(thinking: bool, streaming: bool, perception: bool = False) -> None:
    badges = []
    badges.append("THINK" if thinking else "DIRECT")
    badges.append("STREAM" if streaming else "BATCH")
    badges.append("PERCEP" if perception else "NOPERCEP")
    badge_str = "  ·  ".join(badges)
    print("  +--------------------------------------------+")
    print("  |     WoTAgent  ·  LangChain 1.3+            |")
    print("  |  Event-driven IoT Agent with SSE           |")
    print(f"  |  [{badge_str}]          |")
    print("  |                                            |")
    print("  |  /think  /stream  /devices  /session        |")
    print("  |  /history  /rollback  /new  /perception     |")
    print("  |  /help  /exit                               |")
    print("  +--------------------------------------------+")
    print()


def print_help(thinking: bool, streaming: bool, percept_running: bool = False) -> None:
    print(f"""
  Commands:
    <text>                   Chat with the agent
    /think on|off            Toggle thinking mode      (currently {'ON' if thinking else 'OFF'})
    /stream on|off           Toggle streaming output   (currently {'ON' if streaming else 'OFF'})
    /devices                 List all registered devices
    /session list|new|switch Manage sessions (persist across restarts)
    /history [n]             Show conversation history (last n, or all)
    /rollback <index>        Discard messages after <index>, restart from there
    /perception              Show perception status
    /perception on|off       Toggle perception engine  (currently {'ON' if percept_running else 'OFF'})
    /perception rules        List perception rules
    /perception poll         Trigger one poll cycle now
    /new                     Clear conversation & start fresh
    /help                    This help
    /exit                    Quit
""")


async def run_repl(
    log_mode: str = "file",
    enable_thinking: bool | None = None,
    enable_streaming: bool | None = None,
) -> None:
    """Run the interactive REPL.

    Args:
        log_mode: ``"file"`` — log to wotagent.log (console stays clean);
                  ``"console"`` — log to stderr.
        enable_thinking: Override the env-var default.  ``None`` = read
                         from ``ENABLE_THINKING`` env var.
        enable_streaming: Override the env-var default.  ``None`` = read
                          from ``ENABLE_STREAMING`` env var.
    """
    configure_logging(mode=log_mode)
    install_event_logger()
    if enable_thinking is None:
        enable_thinking = _bool_env("ENABLE_THINKING", default=True)
    if enable_streaming is None:
        enable_streaming = _bool_env("ENABLE_STREAMING", default=True)

    thinking = enable_thinking
    streaming = enable_streaming
    pipeline = create_wot_pipeline(enable_thinking=thinking)
    mgr = get_session_manager(timeout_minutes=30)

    # Start perception engine
    percept_enabled = _bool_env("ENABLE_PERCEPTION", default=True)
    percept_engine = get_perception_engine(
        polling_interval=int(os.getenv("PERCEPTION_INTERVAL", "60")),
        auto_start=percept_enabled,
    )
    percept_running = percept_engine.is_running

    clear_screen()
    print_header(thinking, streaming, percept_running)
    session = mgr.create(pipeline, user_role="operator", session_id="default")
    session.memory.load()  # restore conversation history from disk

    while True:
        try:
            raw = input("wot > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not raw:
            continue

        if raw in ("/exit", "/quit", "exit", "quit"):
            print("Bye!")
            break

        if raw == "/help":
            print_help(thinking, streaming, percept_running)
            continue

        if raw == "/devices":
            tds = load_tds()
            if not tds:
                print("  (no registered devices)")
            else:
                print()
                for td in tds:
                    print(f"  {td.title} @ {td.location}  [{' '.join(td.capabilities)}]")
                    for a in td.actions:
                        print(f"    → {a.name}")
            print()
            continue

        if raw == "/session":
            print(f"  Current session: {session.session_id}")
            print(f"  Messages: {session.memory.message_count}")
            print(f"  Role: {session.user_role}")
            print()
            continue

        if raw.startswith("/session "):
            parts = raw.split(None, 2)
            sub = parts[1] if len(parts) > 1 else ""

            if sub == "list":
                mem_dir = Path(__file__).resolve().parents[2] / "data" / "memory"
                entries = sorted(f.stem for f in mem_dir.glob("*.json")) if mem_dir.exists() else []
                if entries:
                    print("  Available sessions:")
                    for s in entries:
                        marker = "  ← current" if s == session.session_id else ""
                        print(f"    {s}{marker}")
                else:
                    print("  (no saved sessions)")
                print()
                continue

            if sub == "new":
                sid = parts[2] if len(parts) > 2 else None
                session = mgr.create(pipeline, user_role="operator", session_id=sid)
                clear_screen()
                print_header(thinking, streaming, percept_running)
                print(f"  Session '{session.session_id}' started (fresh)")
                print()
                continue

            if sub == "switch":
                if len(parts) < 3:
                    print("  Usage: /session switch <session_id>")
                    print()
                    continue
                target = parts[2]
                existing = mgr.get(target)
                if existing:
                    session = existing
                else:
                    session = mgr.create(pipeline, user_role="operator", session_id=target)
                    session.memory.load()
                clear_screen()
                print_header(thinking, streaming, percept_running)
                print(f"  Switched to '{target}' ({session.memory.message_count} messages)")
                print()
                continue

            print("  Usage: /session list | new [name] | switch <name>")
            print()
            continue

        if raw == "/history":
            msgs = session.memory.messages
            if not msgs:
                print("  (no messages in current session)")
            else:
                print()
                for i, m in enumerate(msgs):
                    role = "user" if "HumanMessage" in type(m).__name__ else "ai" if "AIMessage" in type(m).__name__ else "sys"
                    text = m.content if isinstance(m.content, str) else str(m.content)
                    preview = text[:100].replace("\n", "\\n")
                    print(f"  [{i}] {role}: {preview}")
            print()
            continue

        if raw.startswith("/history "):
            try:
                n = int(raw.split(None, 1)[1])
            except ValueError:
                print("  Usage: /history [n]  (show last n messages)")
                print()
                continue
            msgs = session.memory.messages
            if not msgs:
                print("  (no messages)")
            else:
                print()
                for i, m in enumerate(msgs[-n:]):
                    idx = len(msgs) - n + i
                    role = "user" if "HumanMessage" in type(m).__name__ else "ai" if "AIMessage" in type(m).__name__ else "sys"
                    text = m.content if isinstance(m.content, str) else str(m.content)
                    preview = text[:100].replace("\n", "\\n")
                    print(f"  [{idx}] {role}: {preview}")
            print()
            continue

        if raw.startswith("/rollback "):
            try:
                idx = int(raw.split(None, 1)[1])
            except ValueError:
                print("  Usage: /rollback <message_index>")
                print()
                continue
            msgs = session.memory.messages
            if not msgs:
                print("  (no messages to rollback)")
                print()
                continue
            if idx < 0 or idx >= len(msgs):
                print(f"  Index {idx} out of range (0-{len(msgs) - 1})")
                print()
                continue
            if idx == len(msgs) - 1:
                print("  Already at the last message — nothing to rollback")
                print()
                continue
            session.memory.truncate(idx)
            print(f"  Rolled back to message [{idx}], discarded {len(msgs) - 1 - idx} messages")
            print()
            continue

        if raw in ("/new", "/clear"):
            session = mgr.create(pipeline, user_role="operator")
            clear_screen()
            print_header(thinking, streaming, percept_running)
            print("  (conversation reset)")
            print()
            continue

        if raw == "/think":
            print(f"  Usage: /think on|off  (currently {'ON' if thinking else 'OFF'})")
            print()
            continue

        if raw.startswith("/think "):
            val = raw.split(None, 1)[1].strip().lower()
            new_state = val in ("on", "1", "true", "yes")
            if new_state == thinking:
                print(f"  Thinking mode already {'ON' if thinking else 'OFF'}")
                print()
                continue
            thinking = new_state
            pipeline = create_wot_pipeline(enable_thinking=thinking)
            session = mgr.create(pipeline, user_role="operator")
            clear_screen()
            print_header(thinking, streaming, percept_running)
            print(f"  Thinking mode → {'ON' if thinking else 'OFF'}  (pipeline recreated)")
            print()
            continue

        if raw == "/stream":
            print(f"  Usage: /stream on|off  (currently {'ON' if streaming else 'OFF'})")
            print()
            continue

        if raw.startswith("/stream "):
            val = raw.split(None, 1)[1].strip().lower()
            new_state = val in ("on", "1", "true", "yes")
            if new_state == streaming:
                print(f"  Streaming mode already {'ON' if streaming else 'OFF'}")
                print()
                continue
            streaming = new_state
            clear_screen()
            print_header(thinking, streaming)
            print(f"  Streaming mode → {'ON' if streaming else 'OFF'}")
            print()
            continue

        # ── Perception commands —──────────────────────────────────────

        if raw == "/perception":
            print(f"  Engine: {'RUNNING' if percept_running else 'STOPPED'}")
            print(f"  Polls: {percept_engine.stats['polls']}, Triggers: {percept_engine.stats['triggers']}")
            ctx = percept_engine.get_context()
            if ctx:
                print(f"  Environment: {ctx}")
            print()
            continue

        if raw.startswith("/perception "):
            parts = raw.split(None, 2)
            sub = parts[1] if len(parts) > 1 else ""

            if sub == "on":
                if not percept_running:
                    percept_engine.start()
                    percept_running = True
                    clear_screen()
                    print_header(thinking, streaming, percept_running)
                    print("  Perception engine → ON")
                else:
                    print("  Perception engine already ON")
                print()
                continue

            if sub == "off":
                if percept_running:
                    await percept_engine.stop()
                    percept_running = False
                    clear_screen()
                    print_header(thinking, streaming, percept_running)
                    print("  Perception engine → OFF")
                else:
                    print("  Perception engine already OFF")
                print()
                continue

            if sub == "rules":
                print(f"  Rules ({len(percept_engine.rules)}):")
                for r in percept_engine.rules:
                    status = "ON" if r.enabled else "OFF"
                    cond = f"{r.condition.property_name} {r.condition.operator} {r.condition.value}"
                    ready = "ready" if r.is_ready() else "cooldown"
                    print(f"    [{status}] {r.name}: {r.description}")
                    print(f"           condition: {r.condition.device_id}.{cond} ({ready})")
                print()
                continue

            if sub == "poll":
                import time
                triggered = await percept_engine.poll_once()
                if triggered:
                    for p in triggered:
                        print(f"  ⚡ Rule triggered: {p.get('rationale', '')}")
                        for s in p.get("steps", []):
                            print(f"     → {s.get('action', '')} on {s.get('target', '')}")
                else:
                    print("  No rules triggered")
                print()
                continue

            print("  Usage: /perception        — show status")
            print("         /perception on|off  — start/stop engine")
            print("         /perception rules   — list rules")
            print("         /perception poll    — poll now")
            print()
            continue

        # ── Chat turn —────────────────────────────────────────────────
        last_content = ""   # track cumulative content from AIMessageChunk
        tool_active = False
        streamed = False    # streaming mode printed at least one token
        thinking_printed = ""  # last full thinking text displayed
        thinking_streamed = False
        try:
            async for chunk in invoke_agent_stream(
                session.agent,
                raw,
                session_id=session.session_id,
                user_role=session.user_role,
                memory=session.memory,
                state=session.state,
            ):
                ct = chunk["type"]

                # Close thinking line when moving to non-thinking
                if thinking_streamed and ct != "thinking":
                    print()
                    thinking_streamed = False

                if ct == "plan":
                    plan = chunk["content"]
                    intent = plan.get("intent", "?")
                    steps = plan.get("steps", [])
                    print(f"      📋 Plan: intent={intent}" + (f" ({len(steps)} steps)" if steps else ""))
                    print()
                    continue

                if ct == "thinking":
                    content = chunk["content"]
                    # Extract delta — reasoning_content may be cumulative
                    if content.startswith(thinking_printed):
                        delta = content[len(thinking_printed):]
                    else:
                        delta = content
                    thinking_printed = content
                    if delta:
                        if not thinking_streamed:
                            print(f" 🤔 {delta}", end="", flush=True)
                            thinking_streamed = True
                        else:
                            print(delta, end="", flush=True)
                    
                elif ct == "wot.agent.action.started":
                    inp = chunk["data"].get("input", "")
                    if isinstance(inp, dict):
                        inp = str(inp)[:120]
                    print(f"      🔧 {inp}")
                    tool_active = True

                elif ct == "wot.agent.action.completed":
                    output = chunk["data"].get("output", {})
                    if isinstance(output, dict):
                        if "message" in output:
                            msg = output["message"]
                        elif "error" in output:
                            msg = f"Error: {output['error']}"
                        elif output.get("success") and "device" in output:
                            msg = f"OK ({output['device']})"
                        else:
                            msg = str(output)[:120]
                    elif isinstance(output, list):
                        msg = f"Found {len(output)} device(s)"
                    else:
                        msg = str(output)[:120]
                    if msg:
                        print(f"      ✅ {msg}")
                    tool_active = False

                elif ct == "wot.agent.action.failed":
                    err = str(chunk["data"].get("error", "unknown"))[:120]
                    print(f"      ❌ {err}")
                    tool_active = False

                elif ct == "token":
                    content = chunk["content"]
                    # Extract delta — AIMessageChunk.content is cumulative
                    if len(content) > len(last_content) and content.startswith(last_content):
                        delta = content[len(last_content):]
                    elif not last_content:
                        delta = content
                    else:
                        delta = content
                    last_content = content

                    if streaming:
                        if tool_active:
                            print()
                            tool_active = False
                        # Print delta directly (no \r — content may contain newlines)
                        if not streamed:
                            print(f"  {delta}", end="", flush=True)
                            streamed = True
                        else:
                            print(delta, end="", flush=True)

                elif ct == "error":
                    print(f"  Error: {chunk['content']}")

                elif ct == "done":
                    if streaming and streamed:
                        print()  # close streaming line
                    elif not streaming and last_content:
                        print(f"  {last_content}")
                    elif not last_content and not streamed:
                        print("  (no response)")
                    print()

        except Exception as e:
            print(f"  Error: {e}")
            print()


def main() -> None:
    asyncio.run(run_repl(log_mode="file"))
