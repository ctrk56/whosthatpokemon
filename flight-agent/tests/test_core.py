import json
from datetime import date, datetime

import pytest

from flightagent import airports
from flightagent.config import Config
from flightagent.models import Leg, Offer, Query, Segment
from flightagent.orchestrator import CallBudget, search
from flightagent.providers.mock import MockProvider
from flightagent.ranker import dedupe, rank
from flightagent.tools import ToolError, ToolRunner, build_query

TODAY = date(2026, 11, 16)
CFG = Config()
TRACY = (37.7397, -121.4252)


def seg(fn="AA1", o="DFW", d="LAX", h=8):
    return Segment("AA", fn, o, d, datetime(2026, 12, 1, h), datetime(2026, 12, 1, h + 3))


def offer(price, stops=0, fn="AA1", id_=None):
    segs = tuple(seg(f"{fn}{i}", h=8 + i * 4) for i in range(stops + 1))
    return Offer(id_ or f"o{price}{fn}", "t", price, "USD", Leg(segs), None, 0, "u")


def q(**kw):
    base = dict(origins=("DFW",), destinations=("LAX",), depart_date=date(2026, 12, 1))
    return Query(**{**base, **kw})


def test_rank_lowest_price_first_and_limit():
    offers = [offer(300, fn="A"), offer(100, fn="B"), offer(200, fn="C")]
    assert [o.price for o in rank(offers, q(), 2)] == [100, 200]


def test_rank_filters_max_stops_and_price():
    offers = [offer(100, stops=2, fn="A"), offer(150, stops=0, fn="B"), offer(900, fn="C")]
    out = rank(offers, q(max_stops=0, max_price=500))
    assert [o.price for o in out] == [150]


def test_dedupe_keeps_cheapest_identical_itinerary():
    a, b = offer(200, fn="X", id_="a"), offer(180, fn="X", id_="b")
    assert [o.id for o in dedupe([a, b])] == ["b"]


def test_mock_is_deterministic():
    p = MockProvider()
    assert [o.id for o in p.search_one_way(q())] == [o.id for o in p.search_one_way(q())]


def test_one_way_search_covers_both_home_airports():
    res = search(q(origins=("SCK", "OAK")), [MockProvider()], CallBudget(10))
    assert res["errors"] == [] and res["offers"]
    prices = [o["price"] for o in res["offers"]]
    assert prices == sorted(prices)
    assert {o["outbound"]["route"].split("->")[0] for o in res["offers"]} <= {"SCK", "OAK"}


def test_round_trip_reports_construction_verdict():
    res = search(q(return_date=date(2026, 12, 8)), [MockProvider()], CallBudget(10))
    v = res["construction_verdict"]
    assert v and v["cheaper"] in {"round_trip", "two_one_ways", "tie"}
    assert v["savings"] == round(abs(v["round_trip_best"] - v["two_one_ways_best"]), 2)
    assert res["calls_used"] == 3


def test_budget_exhaustion_is_reported_not_raised():
    res = search(q(return_date=date(2026, 12, 8)), [MockProvider()], CallBudget(1))
    assert any("budget" in e for e in res["errors"])


def test_failing_provider_does_not_break_search():
    class Boom:
        name = "boom"
        def search_one_way(self, q): raise RuntimeError("down")
        search_round_trip = search_one_way

    res = search(q(), [Boom(), MockProvider()], CallBudget(10))
    assert res["offers"] and any("boom" in e for e in res["errors"])


def test_build_query_defaults_and_validation():
    qq = build_query({"destinations": ["lax"], "depart_date": "2026-12-01"}, CFG, TODAY)
    assert qq.origins == ("SCK", "SMF", "OAK", "SFO", "SJC") and qq.destinations == ("LAX",)
    for bad in (
        {"destinations": ["LAX"], "depart_date": "2026-01-01"},
        {"destinations": ["LAX"], "depart_date": "2026-12-05", "return_date": "2026-12-01"},
        {"destinations": ["Los Angeles"], "depart_date": "2026-12-01"},
        {"destinations": ["SFO"], "depart_date": "2026-12-01"},
        {"destinations": ["LAX"], "depart_date": "12/01"},
    ):
        with pytest.raises(ToolError):
            build_query(bad, CFG, TODAY)


def test_tool_runner_returns_errors_as_json():
    r = ToolRunner(CFG, [MockProvider()])
    assert "error" in json.loads(r.call("search_flights", {"destinations": ["LAX"], "depart_date": "bad"}))
    assert "error" in json.loads(r.call("nope", {}))


def test_airports_resolve_and_nearby():
    assert airports.resolve("dfw")[0]["code"] == "DFW"
    assert "DAL" in {a["code"] for a in airports.nearby("DFW", 150)}
    assert {"DFW", "DAL"} <= {a["code"] for a in airports.resolve("Dallas")}


def test_home_airports_near_tracy():
    codes = [a["code"] for a in airports.near_point(*TRACY, 120)]
    assert {"SCK", "SMF", "OAK", "SFO", "SJC"} <= set(codes)
    assert codes[0] == "SCK"
    assert set(CFG.home_airports) <= set(codes)


def test_multi_origin_round_trip_fans_out_and_never_mixes_airports():
    res = search(q(origins=("SCK", "OAK"), return_date=date(2026, 12, 8)),
                 [MockProvider()], CallBudget(40))
    assert res["calls_used"] == 6  # 2 airports x (round trip + 2 one-ways)
    for o in res["offers"]:
        if o["ticketing"] == "two_one_ways":
            assert o["outbound"]["route"].split("->")[0] == o["inbound"]["route"].split("->")[1]
