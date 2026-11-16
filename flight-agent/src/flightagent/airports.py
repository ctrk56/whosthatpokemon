from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def _all() -> list[dict]:
    with resources.files("flightagent.data").joinpath("airports.json").open() as f:
        return json.load(f)


def get(code: str) -> dict | None:
    code = code.upper()
    return next((a for a in _all() if a["code"] == code), None)


def _km(a: dict, b: dict) -> float:
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def nearby(code: str, radius_km: float = 150) -> list[dict]:
    """Other airports within radius_km, nearest first."""
    me = get(code)
    if not me:
        return []
    out = [
        {**a, "distance_km": round(_km(me, a))}
        for a in _all()
        if a["code"] != me["code"] and _km(me, a) <= radius_km
    ]
    return sorted(out, key=lambda a: a["distance_km"])


def resolve(text: str, radius_km: float = 0) -> list[dict]:
    """Match an IATA code, city or airport name. Optionally add nearby airports."""
    t = text.strip().lower()
    exact = [a for a in _all() if a["code"].lower() == t]
    hits = exact or [
        a for a in _all() if t in a["city"].lower() or t in a["name"].lower()
    ]
    if radius_km and hits:
        seen = {a["code"] for a in hits}
        for h in list(hits):
            for n in nearby(h["code"], radius_km):
                if n["code"] not in seen:
                    seen.add(n["code"])
                    hits.append(n)
    return hits


def near_point(lat: float, lon: float, radius_km: float) -> list[dict]:
    """Airports within radius_km of a coordinate, nearest first."""
    pt = {"lat": lat, "lon": lon}
    out = [{**a, "distance_km": round(_km(pt, a))} for a in _all() if _km(pt, a) <= radius_km]
    return sorted(out, key=lambda a: a["distance_km"])
