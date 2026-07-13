"""Analyze how SimuHome goals map to WoTAgent TD action semantics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wotagent.evaluation.simuhome import analyze_simuhome_benchmark


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmark_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = analyze_simuhome_benchmark(args.benchmark_root)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
