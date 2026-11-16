import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import worker from "../src/index.js";
import { buildQuery, parseIgnav, rank, searchFlights, bookingLinks, resolveAirport, nearby } from "../src/search.js";

const LIVE = JSON.parse(readFileSync(new URL("./ignav_live_one_way.json", import.meta.url)));
const LINKS = JSON.parse(readFileSync(new URL("./ignav_booking_links.json", import.meta.url)));
const HOME = ["SCK", "SMF", "OAK", "SFO", "SJC"];
const q = (o = {}) => ({ origins: ["OAK"], destinations: ["LAX"], departDate: "2026-12-10", returnDate: null, adults: 1, cabin: "economy", includeGround: false, ...o });
const jres = (body, status = 200) => new Response(JSON.stringify(body), { status });

test("parses real Ignav response; buses ranked out by default", () => {
  const offers = parseIgnav(LIVE);
  assert.equal(offers.length, 3);
  assert.deepEqual(rank(offers, q()).map((o) => o.price), [93, 161]);
  assert.equal(rank(offers, q({ includeGround: true }))[0].price, 56);
  assert.ok(offers.every((o) => o.checkedBags === null || o.checkedBags === 0));
});

test("buildQuery defaults home airports and validates", () => {
  const b = buildQuery({ destinations: ["lax"], depart_date: "2026-12-10" }, { homeAirports: HOME, today: "2026-11-16" });
  assert.deepEqual(b.origins, HOME);
  for (const bad of [
    { destinations: ["LAX"], depart_date: "2026-01-01" },
    { destinations: ["LAX"], depart_date: "2026-12-10", return_date: "2026-12-01" },
    { destinations: ["Los Angeles"], depart_date: "2026-12-10" },
    { destinations: ["SFO"], depart_date: "2026-12-10" },
    { destinations: ["LAX"], depart_date: "12/10" },
  ]) assert.throws(() => buildQuery(bad, { homeAirports: HOME, today: "2026-11-16" }));
});

test("round trip: 3 calls per airport, no open-jaw pairs, budget respected", async () => {
  const calls = [];
  const fetchFn = async (url, init) => { calls.push([url, JSON.parse(init.body)]); return jres(LIVE); };
  const r = await searchFlights(q({ origins: ["OAK", "SJC"], returnDate: "2026-12-14" }), { fetchFn, key: "k" });
  assert.equal(r.calls_used, 6);
  assert.equal(calls.filter(([u]) => u.endsWith("round-trip")).length, 2);
  assert.ok(r.offers.length > 0);
  const small = await searchFlights(q({ returnDate: "2026-12-14" }), { fetchFn, key: "k" }, 1);
  assert.ok(small.errors.some((e) => e.includes("budget")));
});

test("provider failure is reported, not thrown", async () => {
  const r = await searchFlights(q(), { fetchFn: async () => jres({}, 403), key: "k" });
  assert.deepEqual(r.offers, []);
  assert.ok(r.errors[0].includes("auth failed"));
});

test("booking links send only ignav_id and flatten options", async () => {
  let sent;
  const links = await bookingLinks("ignav:abc", { key: "k", fetchFn: async (u, i) => { sent = JSON.parse(i.body); return jres(LINKS); } });
  assert.deepEqual(sent, { ignav_id: "abc" });
  assert.ok(links[0].url.startsWith("https://"));
});

test("airports", () => {
  assert.equal(resolveAirport("sck")[0].code, "SCK");
  assert.ok(nearby("OAK", 120).some((a) => a.code === "SFO"));
});

// ---- HTTP handler ----
const env = { ALLOWED_ORIGINS: "https://ctrk56.github.io", HOME_AIRPORTS: HOME.join(","), MODEL: "m", CALL_BUDGET: "40", IGNAV_API_KEY: "ik", ANTHROPIC_API_KEY: "ak" };
const req = (body, headers = { Origin: "https://ctrk56.github.io" }, method = "POST") =>
  new Request("https://w.example/chat", { method, headers: { "Content-Type": "application/json", ...headers }, body: method === "POST" ? JSON.stringify(body) : undefined });
const msgs = [{ role: "user", content: "cheapest flight to LAX Dec 10" }];

test("handler: CORS preflight and origin enforcement", async () => {
  const pre = await worker.fetch(req(null, { Origin: "https://ctrk56.github.io" }, "OPTIONS"), env, {});
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get("Access-Control-Allow-Origin"), "https://ctrk56.github.io");
  const bad = await worker.fetch(req({ messages: msgs }, { Origin: "https://evil.example" }), env, {});
  assert.equal(bad.status, 403);
  assert.equal(bad.headers.get("Access-Control-Allow-Origin"), null);
});

test("handler: rejects malformed input", async () => {
  for (const body of [{}, { messages: [] }, { messages: [{ role: "assistant", content: "x" }] },
    { messages: [{ role: "user", content: "x".repeat(3000) }] }, { messages: [{ role: "system", content: "x" }] }]) {
    assert.equal((await worker.fetch(req(body), env, {})).status, 400);
  }
  assert.equal((await worker.fetch(req({ messages: msgs }), { ...env, ANTHROPIC_API_KEY: "" }, {})).status, 503);
});

test("handler: runs the tool loop end to end with fake model + Ignav", async () => {
  const seen = [];
  let modelCalls = 0;
  const fetchFn = async (url, init) => {
    if (url.includes("anthropic.com")) {
      const body = JSON.parse(init.body);
      seen.push(body);
      assert.equal(init.headers["x-api-key"], "ak");
      modelCalls++;
      if (modelCalls === 1) return jres({ stop_reason: "tool_use", content: [{ type: "tool_use", id: "t1", name: "search_flights", input: { destinations: ["LAX"], depart_date: "2099-12-10", origins: ["OAK"] } }] });
      return jres({ stop_reason: "end_turn", content: [{ type: "text", text: "Cheapest is $93." }] });
    }
    assert.equal(init.headers["X-Api-Key"], "ik");
    return jres(LIVE);
  };
  const res = await worker.fetch(req({ messages: msgs }), env, {}, fetchFn);
  assert.equal(res.status, 200);
  assert.equal((await res.json()).reply, "Cheapest is $93.");
  const toolResult = JSON.parse(seen[1].messages.at(-1).content[0].content);
  assert.equal(toolResult.offers[0].price, 93);
  assert.ok(!JSON.stringify(seen).includes("ik\"")); // the Ignav key never reaches the model
});

test("handler: model API failure returns a generic 502 without leaking details", async () => {
  const res = await worker.fetch(req({ messages: msgs }), env, {}, async () => jres({ error: "secret detail" }, 500));
  assert.equal(res.status, 502);
  assert.ok(!(await res.text()).includes("secret"));
});

// ---- M3: date scan ----
import { dateScan, buildScan, evenly } from "../src/search.js";

// Fake Ignav: price by airport offset and weekday (Tuesdays cheap), one itinerary per call.
const OFFSET = { SCK: 0, OAK: 30, SFO: 60, SMF: 90, SJC: 120 };
function fakeIgnav(counter) {
  return async (url, init) => {
    counter.n++;
    const b = JSON.parse(init.body);
    const day = new Date(b.departure_date + "T00:00:00Z");
    const price = 200 + OFFSET[b.origin] - (day.getUTCDay() === 2 ? 80 : 0) + day.getUTCDate();
    const seg = (o, d, dt) => ({ marketing_carrier_code: "AA", flight_number: `AA${dt.slice(8, 10)}`, departure_airport: o, arrival_airport: d,
      departure_time_local: dt + "T09:00:00", arrival_time_local: dt + "T11:00:00", aircraft: "Boeing 737" });
    const it = { ignav_id: `${b.origin}${b.departure_date}`, price: { amount: price, currency: "USD" },
      outbound: { segments: [seg(b.origin, b.destination, b.departure_date)] },
      inbound: b.return_date ? { segments: [seg(b.destination, b.origin, b.return_date)] } : null };
    return new Response(JSON.stringify({ itineraries: [it] }));
  };
}
const D = (n) => new Date(Date.parse("2026-12-01T00:00:00Z") + n * 86400000).toISOString().slice(0, 10);

test("evenly matches the Python implementation", () => {
  const xs = Array.from({ length: 31 }, (_, i) => i);
  assert.deepEqual(evenly(xs, 5), [0, 8, 15, 23, 30]); // JS Math.round(22.5)=23 (Python gives 22)
  assert.deepEqual(evenly(xs, 1), [15]);
});

test("scan: full grid, cheapest is a Tuesday from the cheapest airport", async () => {
  const c = { n: 0 };
  const r = await dateScan(q({ origins: ["SCK", "OAK"] }), D(0), D(6), null, { fetchFn: fakeIgnav(c), key: "k" }, 40);
  assert.equal(r.dates_priced, 7); assert.equal(r.sampled, false); assert.equal(c.n, 14);
  assert.equal(r.cheapest[0].weekday, "Tue"); assert.equal(r.cheapest[0].origin, "SCK");
  assert.ok(r.by_weekday.Tue.min < r.by_weekday.Wed.min);
  assert.ok(r.best_by_airport["SCK->LAX"].price < r.best_by_airport["OAK->LAX"].price);
});

test("scan: budget is never exceeded; probe keeps the cheapest airports", async () => {
  const c = { n: 0 }, calls = [];
  const inner = fakeIgnav(c);
  const fetchFn = async (u, i) => { calls.push(JSON.parse(i.body).origin); return inner(u, i); };
  const r = await dateScan(q({ origins: ["SCK", "OAK", "SFO", "SMF", "SJC"] }), D(0), D(29), null, { fetchFn, key: "k" }, 20);
  assert.ok(c.n <= 20 && r.calls_used <= 20); assert.ok(r.sampled && r.note);
  assert.ok(calls.includes("SCK") && calls.includes("OAK"));
  assert.deepEqual([...r.airports_skipped_after_probe].sort(), ["SFO->LAX", "SJC->LAX", "SMF->LAX"]);
});

test("scan: round trip uses nights and the round-trip endpoint", async () => {
  const urls = [];
  const inner = fakeIgnav({ n: 0 });
  const r = await dateScan(q({ origins: ["SCK"] }), D(0), D(2), 4, { fetchFn: async (u, i) => { urls.push(u); return inner(u, i); }, key: "k" }, 10);
  assert.ok(urls.every((u) => u.endsWith("round-trip")));
  assert.equal(r.grid[0].return, D(4)); assert.equal(r.mode, "round_trip");
});

test("scan: errors reported, month aggregation, validation", async () => {
  const bad = await dateScan(q({ origins: ["SCK"] }), D(0), D(1), null, { fetchFn: async () => jres({}, 500), key: "k" }, 5);
  assert.deepEqual(bad.grid, []); assert.equal(bad.errors.length, 2);
  const r = await dateScan(q({ origins: ["SCK"] }), "2026-12-28", "2027-01-03", null, { fetchFn: fakeIgnav({ n: 0 }), key: "k" }, 20);
  assert.deepEqual(Object.keys(r.by_month).sort(), ["2026-12", "2027-01"]);
  const ctx = { homeAirports: HOME, today: "2026-11-16" };
  assert.throws(() => buildScan({ destinations: ["LAX"], start_date: "2026-12-10", end_date: "2026-12-01" }, ctx));
  assert.throws(() => buildScan({ destinations: ["LAX"], start_date: "2024-01-01", end_date: "2024-02-01" }, ctx));
  assert.throws(() => buildScan({ destinations: ["LAX"], start_date: "2026-12-01", end_date: "2028-12-01" }, ctx));
  const ok = buildScan({ destinations: ["LAX"], start_date: "2026-11-01", end_date: "2026-12-01", nights: 3 }, ctx);
  assert.equal(ok.start, "2026-11-16"); assert.equal(ok.nights, 3); assert.deepEqual(ok.q.origins, HOME);
});

test("search result includes best price per origin airport", async () => {
  const r = await searchFlights(q({ origins: ["OAK", "SJC"] }), { fetchFn: fakeIgnav({ n: 0 }), key: "k" });
  assert.deepEqual(Object.keys(r.best_price_by_origin_airport).sort(), ["OAK", "SJC"]);
});
