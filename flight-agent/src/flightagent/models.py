from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class Query:
    origins: tuple[str, ...]
    destinations: tuple[str, ...]
    depart_date: date
    return_date: date | None = None
    adults: int = 1
    cabin: str = "economy"
    max_stops: int | None = None
    max_price: float | None = None
    airlines_include: tuple[str, ...] = ()
    airlines_exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Segment:
    airline: str
    flight_number: str
    origin: str
    destination: str
    depart: datetime
    arrive: datetime


@dataclass(frozen=True)
class Leg:
    segments: tuple[Segment, ...]

    @property
    def origin(self) -> str:
        return self.segments[0].origin

    @property
    def destination(self) -> str:
        return self.segments[-1].destination

    @property
    def stops(self) -> int:
        return len(self.segments) - 1

    @property
    def duration_minutes(self) -> int:
        return int((self.segments[-1].arrive - self.segments[0].depart).total_seconds() // 60)

    @property
    def airlines(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s.airline for s in self.segments))

    @property
    def max_layover_minutes(self) -> int:
        gaps = [
            int((b.depart - a.arrive).total_seconds() // 60)
            for a, b in zip(self.segments, self.segments[1:])
        ]
        return max(gaps, default=0)


@dataclass(frozen=True)
class Offer:
    id: str
    provider: str
    price: float
    currency: str
    outbound: Leg
    inbound: Leg | None
    checked_bags: int
    booking_url: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Set when an offer is assembled from two separate one-way tickets.
    construction: str = "single"

    @property
    def stops(self) -> int:
        return max(self.outbound.stops, self.inbound.stops if self.inbound else 0)

    @property
    def total_minutes(self) -> int:
        return self.outbound.duration_minutes + (
            self.inbound.duration_minutes if self.inbound else 0
        )

    def summary(self) -> dict:
        """Compact JSON-safe view shown to the model."""

        def leg(l: Leg | None):
            if l is None:
                return None
            return {
                "route": f"{l.origin}->{l.destination}",
                "depart": l.segments[0].depart.isoformat(timespec="minutes"),
                "arrive": l.segments[-1].arrive.isoformat(timespec="minutes"),
                "stops": l.stops,
                "airlines": list(l.airlines),
                "duration_min": l.duration_minutes,
                "max_layover_min": l.max_layover_minutes,
            }

        return {
            "id": self.id,
            "price": round(self.price, 2),
            "currency": self.currency,
            "source": self.provider,
            "ticketing": self.construction,
            "outbound": leg(self.outbound),
            "inbound": leg(self.inbound),
            "checked_bags_included": self.checked_bags,
            "booking_url": self.booking_url,
            "fetched_at": self.fetched_at.isoformat(timespec="seconds"),
        }


def offer_to_dict(o: Offer) -> dict:
    return asdict(o)
