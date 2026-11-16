"""Flexible-date scanning: price grid, cheapest dates, cheapest weekday/month, per-airport view.

Cost control: one provider call per (date, origin, destination). When the full grid would
exceed the call budget, probe the middle date across all airport pairs, keep the cheapest
pair(s), then spread the remaining calls evenly across the window.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta

from .models import Offer, Query
from .orchestrator import CallBudget
from .providers.base import FlightProvider
from .ranker import rank

MAX_WINDOW_DAYS = 366


def evenly(items: list, n: int) -> list:
    """Pick n items spread evenly, always including first and last when n >= 2."""
    if n >= len(items):
        return list(items)
    if n <= 1:
        return [items[len(items) // 2]]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def _price_one(provider: FlightProvider, q: Query, nights: int | None, d: date, o: str, dest: str):
    ret = d + timedelta(days=nights) if nights else None
    qq = replace(q, origins=(o,), destinations=(dest,), depart_date=d, return_date=ret)
    fn = provider.search_round_trip if ret else provider.search_one_way
    best = rank(fn(qq), qq, 1)
    return best[0] if best else None


def date_scan(
    q: Query,
    start: date,
    end: date,
    providers: list[FlightProvider],
    budget: CallBudget,
    nights: int | None = None,
) -> dict:
    if end < start:
        raise ValueError("end date is before start date")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"window longer than {MAX_WINDOW_DAYS} days")
    if nights is not None and nights < 1:
        raise ValueError("nights must be at least 1")

    all_dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    pairs = [(o, d) for o in q.origins for d in q.destinations]
    limit = budget.limit
    results: dict[tuple[date, str, str], Offer | None] = {}
    errors: list[str] = []
    provider = providers[0]  # scans use the primary provider; the rest are for search_flights

    def run(tasks):
        def one(t):
            d, o, dest = t
            if not budget.take():
                return t, None, "call budget exhausted"
            try:
                return t, _price_one(provider, q, nights, d, o, dest), None
            except Exception as e:
                return t, None, f"{type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=max(1, min(8, len(tasks)))) as ex:
            for t, offer, err in ex.map(one, tasks):
                if err:
                    errors.append(f"{provider.name} ({t[1]}->{t[2]} {t[0]}): {err}")
                else:
                    results[t] = offer

    skipped: list[str] = []
    if len(pairs) * len(all_dates) <= limit:
        chosen_pairs, dates = pairs, all_dates
    else:
        probe_date = all_dates[len(all_dates) // 2]
        run([(probe_date, o, d) for o, d in pairs])
        priced = sorted(
            (p for p in pairs if results.get((probe_date, *p))),
            key=lambda p: results[(probe_date, *p)].price,
        )
        # pairs with no probe result sort last, so they are dropped first
        order = priced + [p for p in pairs if p not in priced]
        remaining = limit - budget.used
        chosen_pairs = order[:2] if remaining // 2 >= 3 else order[:1]
        skipped = [f"{o}->{d}" for o, d in order[len(chosen_pairs):]]
        n_dates = max(1, min(len(all_dates), remaining // len(chosen_pairs)))
        dates = sorted(set(evenly(all_dates, n_dates)) | {probe_date}) if n_dates > 1 else [probe_date]

    run([(d, o, dest) for d in dates for o, dest in chosen_pairs if (d, o, dest) not in results])

    # ---- aggregate: cheapest offer per date across airports ----
    per_date: dict[date, tuple[Offer, str, str]] = {}
    for (d, o, dest), offer in results.items():
        if offer and (d not in per_date or offer.price < per_date[d][0].price):
            per_date[d] = (offer, o, dest)

    grid = [
        {
            "depart": d.isoformat(),
            "weekday": d.strftime("%a"),
            "return": (d + timedelta(days=nights)).isoformat() if nights else None,
            "price": off.price,
            "currency": off.currency,
            "origin": o,
            "destination": dest,
            "airlines": list(off.outbound.airlines),
            "stops": off.stops,
            "offer_id": off.id,
        }
        for d, (off, o, dest) in sorted(per_date.items())
    ]

    def agg(key_fn):
        buckets = defaultdict(list)
        for g in grid:
            buckets[key_fn(g)].append(g["price"])
        return {
            k: {"min": min(v), "avg": round(sum(v) / len(v), 2), "samples": len(v)}
            for k, v in buckets.items()
        }

    # Per-airport view uses every priced result, not just each date's winner.
    best_by_airport: dict[str, dict] = {}
    for (d, o, dest), offer in sorted(results.items()):
        if not offer:
            continue
        k = f"{o}->{dest}"
        if k not in best_by_airport or offer.price < best_by_airport[k]["price"]:
            best_by_airport[k] = {
                "price": offer.price,
                "depart": d.isoformat(),
                "return": (d + timedelta(days=nights)).isoformat() if nights else None,
            }

    for k, v in best_by_airport.items():
        v["dates_checked"] = sum(1 for (_, o, dest), off in results.items() if f"{o}->{dest}" == k and off)

    dates_sampled = len(dates) < len(all_dates)
    sampled = dates_sampled or bool(skipped)
    notes = []
    if dates_sampled:
        notes.append("dates were sampled evenly, so cheaper unsampled dates may exist")
    if skipped:
        notes.append(
            "after a one-date probe only the cheapest airports were scanned across the window; "
            "skipped airports show at most one date (dates_checked) and may be cheaper on other dates"
        )
    return {
        "mode": "round_trip" if nights else "one_way",
        "nights": nights,
        "window": [start.isoformat(), end.isoformat()],
        "grid": grid,
        "cheapest": sorted(grid, key=lambda g: (g["price"], g["depart"]))[:5],
        "by_weekday": agg(lambda g: g["weekday"]),
        "by_month": agg(lambda g: g["depart"][:7]),
        "best_by_airport": best_by_airport,
        "dates_priced": len(grid),
        "dates_in_window": len(all_dates),
        "airports_skipped_after_probe": skipped,
        "sampled": sampled,
        "note": ("Budget limited the scan: " + "; ".join(notes) + ".") if notes else None,
        "errors": errors,
        "calls_used": budget.used,
        "call_budget": limit,
    }
