"""
WoTAgent REPL — CLI frontend for the event-driven LangChain WoT agent.

Usage:
    python repl.py          interactive chat
    wotagent repl           same via installed CLI
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path, encoding="utf-8")

from wotagent.repl_impl import main

if __name__ == "__main__":
    main()
