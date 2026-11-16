// Port of the Python flightagent search core: Ignav provider, ranking, round-trip verdict.
import AIRPORTS from "./airports.js";

const GROUND = new Set(["bus", "train", "rail", "ferry", "coach"]);
const IGNAV = "https://ignav.com";

// ---------- airports ----------
export function resolveAirport(text, radiusKm = 0) {
  const t = String(text).trim().toLowerCase();
  const exact = AIRPORTS.filter((a) => a.code.toLowerCase() === t);
  let hits = exact.length ? exact : AIRPORTS.filter((a) => a.city.toLowerCase().includes(t) || a.name.toLowerCase().includes(t));
  if (radiusKm && hits.length) {
    const seen = new Set(hits.map((a) => a.code));
    for (const h of [...hits]) {
      for (const n of nearby(h.code, radiusKm)) if (!seen.has(n.code)) { seen.add(n.code); hits.push(n); }
    }
  }
  return hits.slice(0, 15);
}

function km(a, b) {
  const r = Math.PI / 180, dp = (b.lat - a.lat) * r, dl = (b.lon - a.lon) * r;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dl / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(h));
}

export function nearby(code, radiusKm = 150) {
  const me = AIRPORTS.find((a) => a.code === code.toUpperCase());
  if (!me) return [];
  return AIRPORTS.filter((a) => a.code !== me.code && km(me, a) <= radiusKm)
    .map((a) => ({ ...a, distance_km: Math.round(km(me, a)) }))
    .sort((x, y) => x.distance_km - y.distance_km);
}

// ---------- ignav ----------
const mins = (a, b) => Math.round((Date.parse(b + "Z") - Date.parse(a + "Z")) / 60000);

function parseLeg(raw) {
  const segments = raw.segments.map((s) => ({
    airline: s.marketing_carrier_code,
    flightNumber: String(s.flight_number),
    origin: s.departure_airport,
    destination: s.arrival_airport,
    depart: s.departure_time_local,
    arrive: s.arrival_time_local,
    aircraft: s.aircraft || "",
  }));
  const gaps = segments.slice(1).map((s, i) => mins(segments[i].arrive, s.depart));
  return {
    segments,
    origin: segments[0].origin,
    destination: segments.at(-1).destination,
    stops: segments.length - 1,
    durationMin: mins(segments[0].depart, segments.at(-1).arrive),
    maxLayoverMin: Math.max(0, ...gaps),
    airlines: [...new Set(segments.map((s) => s.airline))],
  };
}

export function parseIgnav(resp) {
  const items = Array.isArray(resp) ? resp : resp?.itineraries || [];
  const out = [];
  for (const it of items) {
    try {
      out.push({
        id: `ignav:${it.ignav_id}`,
        provider: "ignav",
        price: Number(it.price.amount),
        currency: it.price.currency || "USD",
        outbound: parseLeg(it.outbound),
        inbound: it.inbound ? parseLeg(it.inbound) : null,
        checkedBags: it.bags?.checked ?? null, // null = unknown
        construction: "single",
      });
    } catch { /* skip malformed itineraries */ }
  }
  return out;
}

export class ProviderError extends Error {}

async function ignavPost(path, body, { fetchFn, key }) {
  if (!key) throw new ProviderError("IGNAV_API_KEY is not configured");
  let delay = 500;
  for (let attempt = 0; attempt < 3; attempt++) {
    let res;
    try {
      res = await fetchFn(IGNAV + path, {
        method: "POST",
        headers: { "X-Api-Key": key, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      if (attempt < 2) { await new Promise((r) => setTimeout(r, delay)); delay *= 2; continue; }
      throw new ProviderError(`ignav network error: ${e.message}`);
    }
    if (res.ok) return res.json();
    if (res.status === 401 || res.status === 403) throw new ProviderError(`ignav auth failed (HTTP ${res.status})`);
    if ([429, 500, 502, 503, 504].includes(res.status) && attempt < 2) {
      await new Promise((r) => setTimeout(r, delay)); delay *= 2; continue;
    }
    throw new ProviderError(`ignav HTTP ${res.status}`);
  }
  throw new ProviderError("ignav: retries exhausted");
}

function ignavBody(q, origin, dest, date, ret) {
  const b = { origin, destination: dest, departure_date: date, adults: q.adults, cabin_class: q.cabin };
  if (ret) b.return_date = ret;
  if (q.maxStops != null) b.max_stops = q.maxStops;
  if (q.maxPrice != null) b.max_price = Math.trunc(q.maxPrice);
  if (q.airlinesInclude?.length) b.airlines_include = q.airlinesInclude;
  if (q.airlinesExclude?.length) b.airlines_exclude = q.airlinesExclude;
  return b;
}

export async function bookingLinks(offerId, deps) {
  const ids = offerId.split("+");
  const out = [];
  for (const id of ids) {
    const r = await ignavPost("/api/fares/booking-links", { ignav_id: id.replace(/^ignav:/, "") }, deps);
    for (const o of r.booking_options || []) {
      for (const l of o.links || []) {
        out.push({ for: o.legs, provider: l.provider_name, type: l.provider_type, specificity: l.specificity, url: l.url });
      }
    }
  }
  return out;
}

// ---------- ranking ----------
const hasGround = (o) => [o.outbound, o.inbound].some((l) => l?.segments.some((s) => GROUND.has(s.aircraft.trim().toLowerCase())));
const offerStops = (o) => Math.max(o.outbound.stops, o.inbound?.stops ?? 0);
const totalMin = (o) => o.outbound.durationMin + (o.inbound?.durationMin ?? 0);

export function passesFilters(o, q) {
  if (hasGround(o) && !q.includeGround) return false;
  if (q.maxStops != null && offerStops(o) > q.maxStops) return false;
  if (q.maxPrice != null && o.price > q.maxPrice) return false;
  for (const l of [o.outbound, o.inbound]) {
    if (!l) continue;
    if (q.airlinesInclude?.length && !l.airlines.every((a) => q.airlinesInclude.includes(a))) return false;
    if (q.airlinesExclude?.length && l.airlines.some((a) => q.airlinesExclude.includes(a))) return false;
  }
  return true;
}

export function rank(offers, q, limit = 5) {
  const best = new Map();
  for (const o of offers.filter((x) => passesFilters(x, q))) {
    const key = [o.construction, ...[o.outbound, o.inbound].filter(Boolean).map((l) => l.segments.map((s) => s.flightNumber + s.depart).join(","))].join("|");
    if (!best.has(key) || o.price < best.get(key).price) best.set(key, o);
  }
  return [...best.values()].sort((a, b) => a.price - b.price || totalMin(a) - totalMin(b)).slice(0, limit);
}

function summarize(o, fetchedAt) {
  const leg = (l) => l && {
    route: `${l.origin}->${l.destination}`,
    depart: l.segments[0].depart, arrive: l.segments.at(-1).arrive,
    stops: l.stops, airlines: l.airlines, duration_min: l.durationMin, max_layover_min: l.maxLayoverMin,
  };
  return {
    id: o.id, price: o.price, currency: o.currency, source: o.provider, ticketing: o.construction,
    outbound: leg(o.outbound), inbound: leg(o.inbound),
    checked_bags_included: o.checkedBags, includes_ground_transport: hasGround(o), fetched_at: fetchedAt,
  };
}

// ---------- orchestration ----------
export async function searchFlights(q, deps, budgetLimit = 40, limit = 5) {
  const roundTrip = Boolean(q.returnDate);
  const tasks = [];
  for (const o of q.origins) for (const d of q.destinations) {
    if (roundTrip) {
      tasks.push({ kind: "rt", label: `${o}-${d} round-trip`, body: ignavBody(q, o, d, q.departDate, q.returnDate), path: "/api/fares/round-trip" });
      tasks.push({ kind: "out", label: `${o}->${d} one-way`, body: ignavBody(q, o, d, q.departDate), path: "/api/fares/one-way" });
      tasks.push({ kind: "back", label: `${d}->${o} one-way`, body: ignavBody(q, d, o, q.returnDate), path: "/api/fares/one-way" });
    } else {
      tasks.push({ kind: "ow", label: `${o}->${d} one-way`, body: ignavBody(q, o, d, q.departDate), path: "/api/fares/one-way" });
    }
  }
  const errors = [];
  let used = 0;
  const buckets = { rt: [], out: [], back: [], ow: [] };
  await Promise.all(tasks.map(async (t) => {
    if (used >= budgetLimit) { errors.push(`ignav (${t.label}): call budget exhausted`); return; }
    used++;
    try { buckets[t.kind].push(...parseIgnav(await ignavPost(t.path, t.body, deps))); }
    catch (e) { errors.push(`ignav (${t.label}): ${e.message}`); }
  }));

  const candidates = [...buckets.rt, ...buckets.ow];
  const bestRt = rank(buckets.rt, q, 1)[0];
  let bestPair = null;
  if (roundTrip) {
    const outs = rank(buckets.out, { ...q, returnDate: null }, 50);
    const backs = rank(buckets.back, { ...q, returnDate: null }, 50);
    for (const o of outs) for (const b of backs) {
      // Fly home to the airport you left from: no open-jaw mixes.
      if (o.outbound.origin !== b.outbound.destination || o.outbound.destination !== b.outbound.origin) continue;
      const price = Math.round((o.price + b.price) * 100) / 100;
      if (!bestPair || price < bestPair.price) {
        bestPair = { ...o, id: `${o.id}+${b.id}`, price, inbound: b.outbound, construction: "two_one_ways",
          checkedBags: o.checkedBags == null || b.checkedBags == null ? null : Math.min(o.checkedBags, b.checkedBags) };
      }
    }
    if (bestPair) candidates.push(bestPair);
  }
  const fetchedAt = new Date().toISOString().slice(0, 19) + "Z";
  let verdict = null;
  if (bestRt && bestPair) {
    const diff = Math.round((bestPair.price - bestRt.price) * 100) / 100;
    verdict = {
      cheaper: diff === 0 ? "tie" : diff > 0 ? "round_trip" : "two_one_ways",
      round_trip_best: bestRt.price, two_one_ways_best: bestPair.price, savings: Math.abs(diff),
      note: Math.abs(diff) < 50 ? "Under $50 apart: separate tickets are more flexible to change." : null,
    };
  }
  return {
    offers: rank(candidates, q, limit).map((o) => summarize(o, fetchedAt)),
    construction_verdict: verdict, sources_used: ["ignav"], errors, calls_used: used, call_budget: budgetLimit,
  };
}

// ---------- tool input validation ----------
export class ToolError extends Error {}

export function buildQuery(args, { homeAirports, today = new Date().toISOString().slice(0, 10) }) {
  const codes = (list, field) => [...new Set((list || []).map((v) => {
    const c = String(v).trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(c)) throw new ToolError(`${field}: '${v}' is not a 3-letter IATA code; use resolve_airport`);
    return c;
  }))];
  const date = (s, field) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(s)) || Number.isNaN(Date.parse(s))) throw new ToolError(`${field}: '${s}' is not a valid YYYY-MM-DD date`);
    return s;
  };
  const origins = codes(args.origins?.length ? args.origins : homeAirports, "origins");
  const destinations = codes(args.destinations, "destinations");
  if (!destinations.length) throw new ToolError("destinations is required");
  const departDate = date(args.depart_date, "depart_date");
  const returnDate = args.return_date ? date(args.return_date, "return_date") : null;
  if (departDate < today) throw new ToolError(`depart_date ${departDate} is in the past (today is ${today})`);
  if (returnDate && returnDate < departDate) throw new ToolError("return_date is before depart_date");
  if (origins.some((o) => destinations.includes(o))) throw new ToolError("origins and destinations overlap");
  const up = (l) => (l || []).map((a) => String(a).toUpperCase());
  return {
    origins, destinations, departDate, returnDate,
    adults: Math.min(Math.max(parseInt(args.adults) || 1, 1), 9),
    cabin: args.cabin || "economy",
    maxStops: args.max_stops ?? null, maxPrice: args.max_price ?? null,
    airlinesInclude: up(args.airlines_include), airlinesExclude: up(args.airlines_exclude),
    includeGround: Boolean(args.include_ground),
  };
}
