from __future__ import annotations

from typing import Protocol

from ..models import Offer, Query


class FlightProvider(Protocol):
    name: str

    def search_one_way(self, q: Query) -> list[Offer]: ...

    def search_round_trip(self, q: Query) -> list[Offer]: ...


class ProviderError(Exception):
    """Raised when a provider cannot answer (network, auth, rate limit)."""
