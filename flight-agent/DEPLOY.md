# Deploying the flight chat

```
Browser (GitHub Pages: flight-agent/web)  --POST /chat-->  Cloudflare Worker  -->  Anthropic API + Ignav
                                                          (holds both keys)
```

The keys live only in the Worker as secrets. The page contains no secrets.

## 1. Worker (do this first)
```
cd flight-agent/worker && npm install
npx wrangler login
npx wrangler secret put IGNAV_API_KEY        # paste when prompted
npx wrangler secret put ANTHROPIC_API_KEY
# edit wrangler.toml: ALLOWED_ORIGINS = your Pages origin, e.g. https://<user>.github.io
npx wrangler deploy                          # prints https://flightagent.<sub>.workers.dev
```
Rotate the Ignav key you pasted into chat earlier before going live.

## 2. Point the page at the Worker
Set `window.FLIGHT_API` in `flight-agent/web/config.js` to the Worker URL and commit.

## 3. Pages
Repo Settings > Pages > Source: GitHub Actions, then run the "Deploy flight chat to
GitHub Pages" workflow. **Pages serves one site per repo, so this replaces the existing
site in this repo (the Pokemon game).** Prefer moving `flight-agent/` to its own repo.

## Before real traffic
- Add a Cloudflare rate-limiting rule on the Worker route. The in-code limiter is per-isolate and best effort.
- Set a monthly spend limit on the Anthropic key. Each chat message costs model tokens.
- Ignav free tier is 1,000 requests; one 5-airport round-trip search uses 15.
- The origin check blocks other websites but not scripts that fake the Origin header; rate limits and spend caps are the real protection.
