import json
from datetime import date
from pathlib import Path

import pytest

from flightagent.models import Query
from flightagent.providers.base import ProviderError
from flightagent.providers.ignav import IgnavProvider

FIX = json.loads((Path(__file__).parent / "fixtures/ignav_one_way.json").read_text())


def q(**kw):
    base = dict(origins=("OAK",), destinations=("LAX",), depart_date=date(2026, 12, 10))
    return Query(**{**base, **kw})


def make(calls=None):
    def transport(url, headers, body):
        if calls is not None:
            calls.append((url, headers, body))
        return FIX
    return IgnavProvider(api_key="k", transport=transport)


def test_parses_offers_and_skips_malformed():
    offers = make().search_one_way(q())
    assert [o.price for o in offers] == [89.0, 64.0]
    assert offers[0].id == "ignav:abc123" and offers[0].outbound.stops == 0
    assert offers[1].outbound.stops == 1 and offers[1].checked_bags == 1


def test_request_shape_one_way():
    calls = []
    make(calls).search_one_way(q(max_stops=1, airlines_exclude=("NK",)))
    url, headers, body = calls[0]
    assert url.endswith("/api/fares/one-way") and headers["X-Api-Key"] == "k"
    assert body["origin"] == "OAK" and body["destination"] == "LAX"
    assert body["departure_date"] == "2026-12-10" and body["cabin_class"] == "economy"
    assert body["max_stops"] == 1 and body["airlines_exclude"] == ["NK"]


def test_request_shape_round_trip():
    calls = []
    make(calls).search_round_trip(q(return_date=date(2026, 12, 17)))
    url, _, body = calls[0]
    assert url.endswith("/api/fares/round-trip") and body["return_date"] == "2026-12-17"


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("IGNAV_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        IgnavProvider()


def test_unknown_response_shape_returns_empty():
    p = IgnavProvider(api_key="k", transport=lambda *a: {"unexpected": 1})
    assert p.search_one_way(q()) == []
