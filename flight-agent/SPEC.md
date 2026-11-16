# Cheap Flight Agent: Spec (v0.1 draft)

## 1. Goal
A Python chat/CLI agent that finds the **lowest-priced cash fares** for a request, exploring flexible dates and nearby airports, and recommends the top 3-5 options with booking links. **Search and recommend only.** It never books or handles payment.

## 2. Decisions so far
| Area | Decision |
|---|---|
| Form | Python chat/CLI; Claude drives an agent loop with tool calls |
| Ranking | Lowest total price. Stops/duration are filters, not trade-offs |
| Trip types | Round trip, one way, "anywhere" (cheapest destinations) |
| Flexibility | ±N date window, nearby airports, cheapest month/weekday, filters (max stops, airlines, depart times, bags) |
| Output | Top 3-5 in chat: price, dates, stops, airline, booking link |
| Out of scope (v1) | Booking, payments, hidden-city fares, award/points, hotels, price alerts, scraping |
| Reference | `borski/travel-hacking-toolkit` used as **design reference only**. No code vendored |

## 3. Assumed defaults (change if wrong)
- Currency USD, 1 adult, economy, cabin configurable per request.
- Home airport(s) come from `config.toml`. The agent asks if unset.
- Results show a "fetched at" time. Fares are live quotes that can expire.
- Soft budget cap of ~40 provider calls per user request (configurable). The agent says when it is sampling.
- Model: a Claude model via the Anthropic API (`ANTHROPIC_API_KEY`).

## 4. Data sources
All sources sit behind one interface:

```python
class FlightProvider(Protocol):
    name: str
    def search_one_way(q: Query) -> list[Offer]
    def search_round_trip(q: Query) -> list[Offer]
```

| Provider | Role | Notes (from toolkit docs, to verify against live API docs) |
|---|---|---|
| **Ignav** (REST) | Primary | POST `/api/fares/one-way`, `/round-trip`, `/booking-links`. `X-Api-Key` header. Filters: max_stops, max_price, departure_time_range, airlines_include/exclude, `market`. Free tier 1,000 requests |
| **Duffel** (REST) | Primary, trusted price | POST `/air/offer_requests`, header `Duffel-Version: v2`, 60 req/60 s. Multi-slice. `max_connections`. Search is free. Offers expire in ~15-30 min. No market selection |
| **Kiwi MCP** | Optional extra, keyless | Cross-airline routings. Unreliable on small markets, so filter hard |
| Others | Not used in v1 | Scraping, Skiplagged and SerpAPI are excluded |

**Price trust order when sources disagree:** Duffel > Ignav > Kiwi. Always show which source quoted each fare.
**Known gaps:** neither primary source covers Southwest. The agent states this in results for routes Southwest flies.

## 5. Architecture
```
CLI (rich/prompt_toolkit)
  └─ Agent loop (Anthropic SDK, tool use)
       ├─ tools: search_flights, date_scan, nearby_airports, explore_destinations, price_calendar
       └─ Orchestrator
            ├─ query expansion (dates x airports x markets)
            ├─ parallel provider calls (asyncio, per-provider rate limiter)
            ├─ cache (SQLite, TTL ~15 min)
            ├─ normalize -> Offer, dedupe
            └─ ranker (lowest price, filters applied)
```

### Core types
- `Query`: origin(s), destination(s), depart date or window, return date or stay length, pax, cabin, filters, market(s).
- `Offer`: provider, price (amount, currency), legs/segments, stops, duration, airlines, baggage, booking_url, fetched_at, expires_at.

## 6. Tools exposed to the model
| Tool | Purpose |
|---|---|
| `resolve_airport(text)` | City/name to IATA codes, plus nearby airports within a radius (small bundled airport dataset) |
| `search_flights(query)` | Core fare search across providers, returns ranked offers |
| `date_scan(query, window_days)` | Samples date combinations, returns a price grid with cheapest dates, weekdays and month |
| `explore_destinations(origin, window, region?)` | "Anywhere" mode: samples a candidate destination list and returns the cheapest few |
| `get_booking_link(offer_id)` | Fetches or builds the booking link |

The model never sees raw provider JSON, only normalized `Offer`s. This keeps context small and the output consistent.

## 7. Search strategy
1. **Round-trip construction.** For return trips, price both a round-trip fare and a pair of one-ways, then recommend the cheaper one with the delta. If the gap is under ~$50, mention the flexibility of separate tickets.
2. **Date flexibility.** Expand the ±N window into candidate date pairs. Sample coarsely first, then refine around the cheapest cells to stay within the call budget.
3. **Nearby airports.** Expand origin and destination within a configurable radius (default 150 km).
4. **Market arbitrage.** For international routes, try the departure-country market and then the destination-country market. Ask before trying any others. Only Ignav supports `market`.
5. **Anywhere mode.** Use a curated candidate list per region, sample dates, then deepen on the top candidates. Cheapest-month search uses sampled dates across the next 12 months.
6. **Fallbacks.** If a provider errors or rate-limits, continue with the others and report the missing source. Never fail silently. If all fail, say so and suggest retrying.
7. **Zero results.** Distinguish "no availability" from "route not served" where possible, and suggest widening dates.

## 8. Output format
Short ranked table, then a one-line verdict:

| # | Price | Dates | Route | Airline | Stops | Duration | Source |
|---|---|---|---|---|---|---|---|

Followed by booking links, a note on tradeoffs (bags, long layovers), the fetched-at time and any missing sources.
Rules: lead with the cheapest, flag fares with no checked bag, flag overnight layovers over 8 h, and state what was and wasn't searched.

## 9. Config and secrets
- `.env`: `ANTHROPIC_API_KEY`, `IGNAV_API_KEY`, `DUFFEL_API_KEY`. Never logged, never committed.
- `config.toml`: home airports, currency, cabin, call budget, nearby-airport radius, enabled providers.
- The agent works with only one provider key set, with reduced coverage.

## 10. Project layout
```
flight-agent/
  pyproject.toml
  src/flightagent/
    cli.py  agent.py  tools.py  orchestrator.py  ranker.py  cache.py  models.py
    providers/{base,ignav,duffel,kiwi,mock}.py
    data/airports.json
  tests/  (provider contract tests with recorded fixtures, ranker, date expansion)
```

## 11. Testing
- `mock` provider with fixture data for deterministic end-to-end runs without keys.
- Contract tests per provider against recorded responses.
- Unit tests: date-window expansion, round-trip vs one-ways comparison, dedupe, ranker, budget limiter.
- A small eval set of sample prompts checking the tool-call sequence and the output format.

## 12. Milestones
1. **M1:** models, mock provider, ranker, CLI loop answering one-way and round-trip queries.
2. **M2:** Ignav and Duffel providers, parallel calls, cache, fallbacks.
3. **M3:** date flexibility, nearby airports, price grid.
4. **M4:** anywhere mode, market arbitrage, optional Kiwi.
5. **M5:** polish, docs, eval set.

## 13. Risks
- **API access and terms.** Free tiers may change, and Duffel may require a business-style account. Verify both before M2.
- **Quote expiry.** Prices shown may not be bookable later. Show the fetched time.
- **Call volume.** Flexible and anywhere searches multiply calls, so the budget cap and caching are essential.
- **Coverage gaps.** No Southwest and few low-cost carriers. State this plainly in results.
- **Price-trust disagreements** between sources, handled by the trust order and per-fare source labels.

## 14. Open questions
1. Home airport(s), currency and typical cabin?
2. Are Ignav and Duffel keys obtainable? Either alone is enough for M1-M2.
3. Per-search call budget and acceptable latency (a flexible search may take 30-60 s)?
4. Is Kiwi worth including in v1?
5. Where should this live? Currently `flight-agent/` inside this repo. A separate repo may be cleaner.
