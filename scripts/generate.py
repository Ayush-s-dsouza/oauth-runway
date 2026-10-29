"""CLI: generate a guide for one app and print progress.

    python -m scripts.generate "HubSpot"
    python -m scripts.generate "notion.so" --slug notion
"""

import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.pipeline import generate_guide  # noqa: E402


async def _print(ev: dict) -> None:
    print(f"[{ev['type']:>11}] {ev.get('text', '')}"[:220], flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--slug")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    g = asyncio.run(generate_guide(args.query, _print, slug=args.slug))
    print(f"\nsaved {g.slug}: {g.verdict.difficulty} | effort {g.verdict.hands_on_effort} | wait {g.verdict.approval_wait}")


if __name__ == "__main__":
    main()
