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
