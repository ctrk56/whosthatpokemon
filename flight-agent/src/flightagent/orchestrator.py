from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date

from .models import Offer, Query
from .providers.base import FlightProvider
from .ranker import rank


class CallBudget:
    """Thread-safe cap on provider calls per user request."""

    def __init__(self, limit: int):
        self.limit, self.used = limit, 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


def _combine(out: Offer, back: Offer) -> Offer:
    return Offer(
        id=f"{out.id}+{back.id}",
        provider=f"{out.provider}+{back.provider}" if out.provider != back.provider else out.provider,
        price=round(out.price + back.price, 2),
        currency=out.currency,
        outbound=out.outbound,
        inbound=back.outbound,
        checked_bags=min(out.checked_bags, back.checked_bags),
        booking_url=f"{out.booking_url} | {back.booking_url}",
        construction="two_one_ways",
    )


def search(
    q: Query,
    providers: list[FlightProvider],
    budget: CallBudget,
    limit: int = 5,
) -> dict:
    """Search all providers in parallel.

    For return trips, price a round-trip fare and a pair of one-ways, and report
    which construction is cheaper.
    """
    errors: list[str] = []
    tasks: list[tuple[str, str, FlightProvider, Query]] = []
    round_trip = q.return_date is not None
    for p in providers:
        if round_trip:
            tasks.append(("rt", "round-trip", p, q))
            tasks.append(("out", "outbound one-way", p, replace(q, return_date=None)))
            tasks.append((
                "back", "return one-way", p,
                replace(q, origins=q.destinations, destinations=q.origins,
                        depart_date=q.return_date, return_date=None),
            ))
        else:
            tasks.append(("ow", "one-way", p, q))

    def run(task):
        kind, label, p, qq = task
        if not budget.take():
            return kind, label, p, None, "call budget exhausted"
        try:
            fn = p.search_round_trip if kind == "rt" else p.search_one_way
            return kind, label, p, fn(qq), None
        except Exception as e:  # providers must never crash the whole search
            return kind, label, p, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(tasks)))) as ex:
        results = list(ex.map(run, tasks))

    buckets: dict[str, list[Offer]] = {"rt": [], "out": [], "back": [], "ow": []}
    for kind, label, p, offers, err in results:
        if err:
            errors.append(f"{p.name} ({label}): {err}")
        else:
            buckets[kind].extend(offers)

    candidates = buckets["rt"] + buckets["ow"]
    best_rt = rank(buckets["rt"], q, 1)
    best_pair = None
    if round_trip:
        outs = rank(buckets["out"], replace(q, return_date=None), 1)
        backs = rank(
            buckets["back"],
            replace(q, origins=q.destinations, destinations=q.origins, return_date=None),
            1,
        )
        if outs and backs:
            best_pair = _combine(outs[0], backs[0])
            candidates.append(best_pair)

    top = rank(candidates, q, limit)
    verdict = None
    if best_rt and best_pair:
        diff = round(best_pair.price - best_rt[0].price, 2)
        cheaper = "round_trip" if diff > 0 else "two_one_ways"
        verdict = {
            "cheaper": cheaper if diff != 0 else "tie",
            "round_trip_best": best_rt[0].price,
            "two_one_ways_best": best_pair.price,
            "savings": abs(diff),
            "note": "Under $50 apart: separate tickets are more flexible to change."
            if abs(diff) < 50 else None,
        }

    return {
        "offers": [o.summary() for o in top],
        "construction_verdict": verdict,
        "sources_used": sorted({p.name for p in providers}),
        "errors": errors,
        "calls_used": budget.used,
        "call_budget": budget.limit,
    }


def parse_date(s: str) -> date:
    return date.fromisoformat(s)
