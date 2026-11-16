from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

from . import config as config_mod
from .agent import Agent
from .providers import build_providers
from .tools import ToolRunner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flightagent", description="Cheapest-flight chat agent")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--raw", metavar="JSON",
                    help="Skip the LLM: run a tool with these JSON args and print the result")
    ap.add_argument("--tool", default="search_flights",
                    help="Tool for --raw: search_flights (default), date_scan, resolve_airport")
    args = ap.parse_args(argv)

    config_mod.load_dotenv()
    cfg = config_mod.load(args.config)
    runner = ToolRunner(cfg, build_providers(cfg.providers))

    if args.raw:
        print(json.dumps(json.loads(runner.call(args.tool, json.loads(args.raw))), indent=2))
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY (or use --raw to test the search without the LLM).",
              file=sys.stderr)
        return 1

    agent = Agent(cfg, runner)
    print(f"Flight agent ({date.today()}). Home: {', '.join(cfg.home_airports)}. "
          f"Sources: {', '.join(cfg.providers)}. Ctrl-D to quit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text:
            print("\n" + agent.ask(text, on_tool=lambda n, a: print(f"  [{n} {json.dumps(a)}]")) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
