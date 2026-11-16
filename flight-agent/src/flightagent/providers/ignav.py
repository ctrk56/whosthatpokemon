"""Ignav fare-search provider (https://ignav.com).

Endpoints and field names follow the public skill docs of the travel-hacking-toolkit
and have NOT yet been verified against live responses. The parser is deliberately
tolerant, and `tests/fixtures/` should be replaced with real recorded responses
once a live call succeeds.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable

from ..models import Leg, Offer, Query, Segment
from .base import ProviderError

BASE = "https://ignav.com"
# transport(url, headers, body) -> parsed JSON
Transport = Callable[[str, dict, dict], dict]


def _http(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    delay = 1.0
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ProviderError(f"ignav auth failed (HTTP {e.code}); check IGNAV_API_KEY")
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(delay)
                delay *= 2
                continue
            raise ProviderError(f"ignav HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 2:
                time.sleep(delay)
                delay *= 2
                continue
            raise ProviderError(f"ignav network error: {e}")
    raise ProviderError("ignav: retries exhausted")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def _leg(raw: dict) -> Leg:
    segs = tuple(
        Segment(
            airline=s["marketing_carrier_code"],
            flight_number=str(s["flight_number"]),
            origin=s["departure_airport"],
            destination=s["arrival_airport"],
            depart=_dt(s["departure_time_local"]),
            arrive=_dt(s["arrival_time_local"]),
            aircraft=s.get("aircraft") or "",
        )
        for s in raw["segments"]
    )
    return Leg(segs)


def _items(resp: dict | list) -> list[dict]:
    if isinstance(resp, list):
        return resp
    for k in ("itineraries", "fares", "results", "data"):
        if isinstance(resp.get(k), list):
            return resp[k]
    return []


class IgnavProvider:
    name = "ignav"

    def __init__(self, api_key: str | None = None, transport: Transport | None = None,
                 market: str = "US"):
        self.api_key = api_key or os.environ.get("IGNAV_API_KEY", "")
        if not self.api_key and transport is None:
            raise ProviderError("IGNAV_API_KEY is not set")
        self.transport = transport or _http
        self.market = market

    def _call(self, path: str, body: dict) -> dict:
        headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        return self.transport(BASE + path, headers, body)

    def _body(self, q: Query, origin: str, dest: str) -> dict:
        body = {
            "origin": origin,
            "destination": dest,
            "departure_date": q.depart_date.isoformat(),
            "adults": q.adults,
            "cabin_class": q.cabin,
            "market": self.market,
        }
        # Ignav also returns buses (e.g. Flixbus); the ranker drops them unless include_ground.
        if q.max_stops is not None:
            body["max_stops"] = q.max_stops
        if q.max_price is not None:
            body["max_price"] = int(q.max_price)
        if q.airlines_include:
            body["airlines_include"] = list(q.airlines_include)
        if q.airlines_exclude:
            body["airlines_exclude"] = list(q.airlines_exclude)
        return body

    def _parse(self, resp: dict | list) -> list[Offer]:
        offers = []
        for it in _items(resp):
            try:
                price = it["price"]
                offers.append(Offer(
                    id=f"ignav:{it['ignav_id']}",
                    provider=self.name,
                    price=float(price["amount"]),
                    currency=price.get("currency", "USD"),
                    outbound=_leg(it["outbound"]),
                    inbound=_leg(it["inbound"]) if it.get("inbound") else None,
                    checked_bags=(it.get("bags") or {}).get("checked"),
                    booking_url="",  # resolved on demand via booking_link()
                ))
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed itineraries rather than failing the search
        return offers

    # A "query" here always has exactly one origin and one destination;
    # the orchestrator fans multi-airport searches out into pairs.
    def search_one_way(self, q: Query) -> list[Offer]:
        return self._parse(self._call("/api/fares/one-way", self._body(q, q.origins[0], q.destinations[0])))

    def search_round_trip(self, q: Query) -> list[Offer]:
        body = self._body(q, q.origins[0], q.destinations[0])
        body["return_date"] = q.return_date.isoformat()
        return self._parse(self._call("/api/fares/round-trip", body))

    def booking_link(self, offer_id: str) -> list[dict] | dict:
        return self._call(
            "/api/fares/booking-links",
            {"ignav_id": offer_id.removeprefix("ignav:")},  # takes no passenger fields (HTTP 400)
        )
