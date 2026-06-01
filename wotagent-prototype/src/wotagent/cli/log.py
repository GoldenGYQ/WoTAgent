"""WoTAgent Log Viewer — tails the wotagent.log file in real-time.

Usage:
    wotagent log               Tail wotagent.log
    wotagent log --no-follow   Print current contents and exit
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path


async def run_log_viewer(log_file: str = "wotagent.log", follow: bool = True) -> None:
    """Print log file contents, optionally following for new lines.

    Args:
        log_file: Path to the log file.
        follow: If True, tail the file continuously. If False, print and exit.
    """
    path = Path(log_file).resolve()
    if not path.exists():
        print(f"  Log file not found: {path}")
        print(f"  Start wotagent repl or api first to generate logs.")
        return

    if not follow:
        # Print-and-exit mode
        content = path.read_text(encoding="utf-8")
        if content:
            print(content.rstrip())
        else:
            print(f"  (log file is empty: {path})")
        return

    # Follow mode — print existing content then tail
    print(f"  Tailing {path}  (Ctrl+C to stop)")
    print(f"  {'─' * 50}")

    with path.open("r", encoding="utf-8") as fh:
        # Print existing content
        content = fh.read()
        if content:
            print(content.rstrip())

        # Poll for new content
        while True:
            line = fh.readline()
            if line:
                print(line.rstrip())
            else:
                await asyncio.sleep(0.2)
