from __future__ import annotations

from .models import Offer, Query


def passes_filters(o: Offer, q: Query) -> bool:
    if o.has_ground and not q.include_ground:
        return False
    if q.max_stops is not None and o.stops > q.max_stops:
        return False
    if q.max_price is not None and o.price > q.max_price:
        return False
    legs = [o.outbound] + ([o.inbound] if o.inbound else [])
    for leg in legs:
        if q.airlines_include and not set(leg.airlines) <= set(q.airlines_include):
            return False
        if q.airlines_exclude and set(leg.airlines) & set(q.airlines_exclude):
            return False
    return True


def dedupe(offers: list[Offer]) -> list[Offer]:
    """Keep the cheapest offer per identical itinerary (same flights and construction)."""
    best: dict[tuple, Offer] = {}
    for o in offers:
        legs = [o.outbound] + ([o.inbound] if o.inbound else [])
        key = (o.construction,) + tuple(
            tuple((s.flight_number, s.depart) for s in leg.segments) for leg in legs
        )
        if key not in best or o.price < best[key].price:
            best[key] = o
    return list(best.values())


def rank(offers: list[Offer], q: Query, limit: int = 5) -> list[Offer]:
    """Lowest price first. Duration only breaks ties."""
    kept = dedupe([o for o in offers if passes_filters(o, q)])
    kept.sort(key=lambda o: (o.price, o.total_minutes))
    return kept[:limit]
