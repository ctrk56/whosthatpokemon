from __future__ import annotations

import json
from datetime import date

from . import airports
from .config import Config
from .models import Query
from .orchestrator import CallBudget, search
from .providers.base import FlightProvider

TOOLS = [
    {
        "name": "resolve_airport",
        "description": (
            "Look up airports by IATA code, city or airport name. Set radius_km to also "
            "return nearby alternative airports (nearest first)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "radius_km": {"type": "number", "description": "0 = no nearby airports"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "search_flights",
        "description": (
            "Search cash fares across providers and return the cheapest options, lowest "
            "price first. Leave return_date empty for one-way. For return trips this "
            "also compares a round-trip fare against two one-ways. Origins default to "
            "the user's home airports."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origins": {"type": "array", "items": {"type": "string"},
                            "description": "IATA codes; omit to use home airports"},
                "destinations": {"type": "array", "items": {"type": "string"}},
                "depart_date": {"type": "string", "description": "YYYY-MM-DD"},
                "return_date": {"type": "string", "description": "YYYY-MM-DD, optional"},
                "adults": {"type": "integer", "minimum": 1},
                "cabin": {"type": "string",
                          "enum": ["economy", "premium_economy", "business", "first"]},
                "max_stops": {"type": "integer", "minimum": 0, "maximum": 2},
                "max_price": {"type": "number"},
                "airlines_include": {"type": "array", "items": {"type": "string"}},
                "airlines_exclude": {"type": "array", "items": {"type": "string"}},
                "include_ground": {"type": "boolean",
                                   "description": "Include buses/trains. Default false (flights only)."},
            },
            "required": ["destinations", "depart_date"],
        },
    },
]


class ToolError(Exception):
    pass


def _codes(values, field: str) -> tuple[str, ...]:
    out = []
    for v in values:
        c = str(v).strip().upper()
        if len(c) != 3 or not c.isalpha():
            raise ToolError(f"{field}: '{v}' is not a 3-letter IATA code; use resolve_airport")
        out.append(c)
    return tuple(dict.fromkeys(out))


def _date(s: str, field: str) -> date:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        raise ToolError(f"{field}: '{s}' is not a valid YYYY-MM-DD date")


def build_query(args: dict, cfg: Config, today: date | None = None) -> Query:
    today = today or date.today()
    origins = _codes(args.get("origins") or cfg.home_airports, "origins")
    dests = _codes(args.get("destinations") or [], "destinations")
    if not dests:
        raise ToolError("destinations is required")
    depart = _date(args.get("depart_date"), "depart_date")
    ret = _date(args["return_date"], "return_date") if args.get("return_date") else None
    if depart < today:
        raise ToolError(f"depart_date {depart} is in the past (today is {today})")
    if ret and ret < depart:
        raise ToolError("return_date is before depart_date")
    if set(origins) & set(dests):
        raise ToolError("origins and destinations overlap")
    return Query(
        origins=origins,
        destinations=dests,
        depart_date=depart,
        return_date=ret,
        adults=int(args.get("adults") or 1),
        cabin=args.get("cabin") or cfg.cabin,
        max_stops=args.get("max_stops"),
        max_price=args.get("max_price"),
        airlines_include=tuple(a.upper() for a in args.get("airlines_include") or ()),
        airlines_exclude=tuple(a.upper() for a in args.get("airlines_exclude") or ()),
        include_ground=bool(args.get("include_ground", False)),
    )


class ToolRunner:
    def __init__(self, cfg: Config, providers: list[FlightProvider]):
        self.cfg, self.providers = cfg, providers

    def call(self, name: str, args: dict) -> str:
        """Run a tool. Always returns a JSON string; errors are returned, not raised."""
        try:
            if name == "resolve_airport":
                hits = airports.resolve(args["text"], float(args.get("radius_km") or 0))
                result = {"matches": hits[:15]}
            elif name == "search_flights":
                q = build_query(args, self.cfg)
                result = search(q, self.providers, CallBudget(self.cfg.call_budget))
            else:
                raise ToolError(f"unknown tool {name}")
        except ToolError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
        return json.dumps(result)
