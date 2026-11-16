"""Deterministic fake provider so the agent runs end to end without API keys."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta

from ..models import Leg, Offer, Query, Segment

CARRIERS = ["AA", "WN", "DL", "UA", "NK", "F9"]
HUBS = ["ATL", "DEN", "ORD", "PHX", "CLT"]


def _rng(*parts) -> int:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return int(h[:12], 16)


def _leg(origin: str, dest: str, day: date, seed: int, stops: int) -> Leg:
    start = datetime.combine(day, time(5 + seed % 14, (seed // 7) % 4 * 15))
    hubs = [h for h in HUBS if h not in (origin, dest)]
    stops_at = [origin] + [hubs[(seed + i) % len(hubs)] for i in range(stops)] + [dest]
    segs, t = [], start
    for i in range(len(stops_at) - 1):
        dur = timedelta(minutes=90 + (seed >> (i + 2)) % 150)
        carrier = CARRIERS[(seed >> i) % len(CARRIERS)]
        segs.append(
            Segment(carrier, f"{carrier}{100 + (seed >> (i + 3)) % 900}",
                    stops_at[i], stops_at[i + 1], t, t + dur)
        )
        t += dur + timedelta(minutes=50 + (seed >> (i + 5)) % 150)
    return Leg(tuple(segs))


class MockProvider:
    name = "mock"

    def _build(self, q: Query, round_trip: bool) -> list[Offer]:
        offers: list[Offer] = []
        for o in q.origins:
            for d in q.destinations:
                for n in range(5):
                    seed = _rng(o, d, q.depart_date, q.return_date if round_trip else "", n)
                    stops = (seed >> 4) % 3
                    out = _leg(o, d, q.depart_date, seed, stops)
                    back = (
                        _leg(d, o, q.return_date, seed >> 3, (seed >> 6) % 3)
                        if round_trip and q.return_date else None
                    )
                    base = 70 + seed % 260 - stops * 20
                    price = float(max(49, base * (1.7 if back else 1)) * q.adults)
                    offers.append(
                        Offer(
                            id=f"mock-{seed:x}",
                            provider=self.name,
                            price=round(price, 2),
                            currency="USD",
                            outbound=out,
                            inbound=back,
                            checked_bags=(seed >> 9) % 2,
                            booking_url=f"https://example.com/book/{seed:x}",
                        )
                    )
        return offers

    def search_one_way(self, q: Query) -> list[Offer]:
        return self._build(q, round_trip=False)

    def search_round_trip(self, q: Query) -> list[Offer]:
        return self._build(q, round_trip=True)
