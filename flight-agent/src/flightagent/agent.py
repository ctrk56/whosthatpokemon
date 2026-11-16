from __future__ import annotations

from datetime import date

import anthropic

from .config import Config
from .tools import TOOLS, ToolRunner

MAX_TURNS = 10


def system_prompt(cfg: Config, today: date | None = None) -> str:
    today = today or date.today()
    return f"""You are a flight-search agent whose only goal is the LOWEST cash price.
Today is {today.isoformat()}. User's home airports: {", ".join(cfg.home_airports)}. \
Currency {cfg.currency}, default cabin {cfg.cabin}.

Rules:
- Search first, then answer. Use search_flights for fares; never invent prices, flights or links.
- Ask only when essential info is missing (destination or dates). Otherwise use the defaults.
- Rank purely by price. Stops/duration are filters the user asks for, not trade-offs.
- Show the top 3-5 options as a short table: price, dates, route, airline, stops, source, booking link.
- For return trips, report whether a round trip or two one-way tickets is cheaper and by how much.
- Flag: no checked bag included, layovers over 8 hours, offers assembled from two separate tickets.
- State the fetched-at time (fares can expire) and any sources that errored or hit the call budget.
- You never book or take payment; give links so the user can book.
- Data sources currently: {", ".join(cfg.providers)}. If this is 'mock', tell the user prices are sample data, not real fares."""


class Agent:
    def __init__(self, cfg: Config, runner: ToolRunner, client: anthropic.Anthropic | None = None):
        self.cfg, self.runner = cfg, runner
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict] = []

    def ask(self, text: str, on_tool=None) -> str:
        self.messages.append({"role": "user", "content": text})
        for _ in range(MAX_TURNS):
            resp = self.client.messages.create(
                model=self.cfg.model,
                max_tokens=2048,
                system=system_prompt(self.cfg),
                tools=TOOLS,
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text")
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    if on_tool:
                        on_tool(b.name, b.input)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": self.runner.call(b.name, b.input),
                    })
            self.messages.append({"role": "user", "content": results})
        return "Stopped: too many tool calls without a final answer. Try a narrower request."
