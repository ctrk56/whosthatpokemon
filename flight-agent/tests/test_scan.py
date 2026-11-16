import json
from datetime import date, datetime, timedelta

import pytest

from flightagent.config import Config
from flightagent.models import Leg, Offer, Segment
from flightagent.orchestrator import CallBudget
from flightagent.scan import date_scan, evenly
from flightagent.tools import ToolError, ToolRunner, build_scan

import flightagent.tools as tools_mod

START = date(2026, 12, 1)


class FakeProvider:
    """Price = 200 + airport offset - discount on Tuesdays; counts calls."""
    name = "fake"
    OFFSET = {"SCK": 0, "OAK": 30, "SFO": 60, "SMF": 90, "SJC": 120}

    def __init__(self):
        self.calls = []

    def _offer(self, q, rt):
        o, d, day = q.origins[0], q.destinations[0], q.depart_date
        price = 200 + self.OFFSET[o] - (80 if day.weekday() == 1 else 0) + day.day
        t = datetime.combine(day, datetime.min.time()).replace(hour=9)
        leg = Leg((Segment("AA", f"AA{day.day}", o, d, t, t + timedelta(hours=2)),))
        back = Leg((Segment("AA", "AA9", d, o, t, t + timedelta(hours=2)),)) if rt else None
        return Offer(f"f-{o}-{day}", "fake", float(price), "USD", leg, back, None, "u")

    def search_one_way(self, q):
        self.calls.append((q.origins[0], q.depart_date, False))
        return [self._offer(q, False)]

    def search_round_trip(self, q):
        self.calls.append((q.origins[0], q.depart_date, True))
        return [self._offer(q, True)]


def q(origins=("SCK", "OAK"), dests=("LAX",)):
    from flightagent.models import Query
    return Query(origins=origins, destinations=dests, depart_date=START)


def test_evenly_includes_endpoints():
    xs = list(range(31))
    assert evenly(xs, 5) == [0, 8, 15, 22, 30]
    assert evenly(xs, 40) == xs and evenly(xs, 1) == [15]


def test_full_grid_when_budget_allows_and_finds_tuesday():
    p = FakeProvider()
    r = date_scan(q(), START, START + timedelta(days=6), [p], CallBudget(40))
    assert r["dates_priced"] == 7 and not r["sampled"] and r["calls_used"] == 14
    assert r["cheapest"][0]["weekday"] == "Tue" and r["cheapest"][0]["origin"] == "SCK"
    assert r["by_weekday"]["Tue"]["min"] < r["by_weekday"]["Wed"]["min"]
    assert r["best_by_airport"]["SCK->LAX"]["price"] < r["best_by_airport"]["OAK->LAX"]["price"]


def test_budget_never_exceeded_and_probe_keeps_cheapest_airports():
    p = FakeProvider()
    r = date_scan(q(("SCK", "OAK", "SFO", "SMF", "SJC")), START, START + timedelta(days=29),
                  [p], CallBudget(20))
    assert len(p.calls) <= 20 and r["calls_used"] <= 20
    assert r["sampled"] and r["note"]
    searched = {c[0] for c in p.calls}
    assert {"SCK", "OAK"} <= searched
    assert set(r["airports_skipped_after_probe"]) == {"SFO->LAX", "SMF->LAX", "SJC->LAX"}
    assert r["dates_priced"] >= 5


def test_round_trip_uses_nights_and_round_trip_endpoint():
    p = FakeProvider()
    r = date_scan(q(("SCK",)), START, START + timedelta(days=2), [p], CallBudget(10), nights=4)
    assert all(c[2] for c in p.calls)
    assert r["grid"][0]["return"] == (START + timedelta(days=4)).isoformat()
    assert r["mode"] == "round_trip"


def test_month_aggregation_across_months():
    p = FakeProvider()
    r = date_scan(q(("SCK",)), date(2026, 12, 28), date(2027, 1, 3), [p], CallBudget(20))
    assert set(r["by_month"]) == {"2026-12", "2027-01"}


def test_provider_error_is_reported():
    class Boom(FakeProvider):
        def search_one_way(self, q): raise RuntimeError("down")
    r = date_scan(q(("SCK",)), START, START + timedelta(days=1), [Boom()], CallBudget(5))
    assert r["grid"] == [] and len(r["errors"]) == 2


def test_validation():
    cfg = Config()
    today = date(2026, 11, 16)
    with pytest.raises(ToolError):
        build_scan({"destinations": ["LAX"], "start_date": "2026-12-10", "end_date": "2026-12-01"}, cfg, today)
    with pytest.raises(ToolError):
        build_scan({"destinations": ["LAX"], "start_date": "2024-01-01", "end_date": "2024-02-01"}, cfg, today)
    with pytest.raises(ToolError):
        build_scan({"destinations": ["LAX"], "start_date": "2026-12-01", "end_date": "2028-12-01"}, cfg, today)
    qq, s, e, n = build_scan({"destinations": ["LAX"], "start_date": "2026-11-01", "end_date": "2026-12-01", "nights": 3}, cfg, today)
    assert s == today and n == 3 and qq.origins == tuple(cfg.home_airports)


def test_tool_runner_date_scan_with_mock_provider():
    from flightagent.providers.mock import MockProvider
    start = date.today() + timedelta(days=30)
    r = ToolRunner(Config(call_budget=15), [MockProvider()]).call(
        "date_scan", {"destinations": ["LAX"], "start_date": start.isoformat(),
                      "end_date": (start + timedelta(days=13)).isoformat()})
    out = json.loads(r)
    assert "error" not in out and out["calls_used"] <= 15 and out["grid"]
