# flightagent

Chat/CLI agent that finds the cheapest cash fares from airports near Tracy, CA
(SCK, SMF, OAK, SFO, SJC). Search and recommend only. See `SPEC.md`.

```
pip install -e '.[dev]'
cp .env.example .env        # add IGNAV_API_KEY (and ANTHROPIC_API_KEY for chat)
pytest
flightagent --raw '{"destinations":["LAX"],"depart_date":"2026-12-10"}'   # no LLM needed
flightagent                                                              # chat
```

Set `providers = ["mock"]` in `config.toml` for offline sample data.

Status: Ignav provider verified live (one-way and round trip, 5 home airports).
Ignav also returns buses (Flixbus etc.); they are excluded unless `include_ground` is set.
Baggage is often absent from Ignav responses and is reported as unknown.
