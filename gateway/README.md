# ARTie LinkedIn gateway

A small hosted service that fetches a public LinkedIn profile through Bright Data on ART's own key, so a user gives ART a LinkedIn URL and never needs a key. Issue #354. The client is the harness tool in #355; the GitHub token comes from `artie github login` (#353).

It deploys as its own Vercel project (`artie-gateway`), apart from `site/`, so the landing site stays static. It uses the Python runtime and the standard library only: no `requirements.txt`, no import from the rest of the repo.

## Endpoint

`POST /v1/linkedin`

```http
POST /v1/linkedin
Authorization: Bearer <GitHub token>
Content-Type: application/json

{"url": "https://www.linkedin.com/in/jane-example"}
```

`200` returns Bright Data's profile record as JSON, unchanged. Every other status returns `{"error": {"code": "...", "message": "..."}}` with a short fixed message: never an upstream body.

| Status | `code` | When |
|---|---|---|
| 200 | | The profile record |
| 400 | `invalid_url`, `invalid_request` | Not JSON with a `url`, or the URL is not `linkedin.com/in/<name>` |
| 401 | `unauthorized` | No token, a malformed one, or one GitHub rejects |
| 405 | `method_not_allowed` | Anything but POST |
| 413 | `too_large` | Body over 4 KB |
| 429 | `quota_exceeded`, `rate_limited` | Over the user's daily quota, or the per-IP limit. `Retry-After` says when |
| 500 | `internal` | A bug; the message is generic |
| 502 | `upstream_error`, `upstream_unreachable`, `github_error`, `github_unreachable` | Bright Data or GitHub failed or returned nothing usable |
| 503 | `spend_cap_reached`, `not_configured`, `unavailable` | The monthly cap is hit, the service is missing its key or KV store, or the KV store is down |
| 504 | `upstream_timeout`, `github_timeout` | Bright Data or GitHub did not answer in time |

How it works, in order: IP rate limit, then the `Authorization` header, then the body and URL (`urls.py`), then `GET https://api.github.com/user` (cached five minutes under a hash of the token), then the user's daily quota, then the monthly spend cap, then the Bright Data call. The counters key on the GitHub numeric user id. A fetch that fails refunds the user's daily slot but not the spend count, so the cap errs safe. The KV store being down fails closed with 503.

Privacy: no request body, response body, token or URL is logged (one fixed line per request: outcome, status, milliseconds), and nothing about a profile is stored. The store holds counters and a hashed-token cache, all with expiry. `tests/test_gw_privacy.py` checks this by capturing logs. The public statement is `site/src/pages/privacy.astro`.

## Environment variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `BRIGHTDATA_API_KEY` | yes | | Bright Data API key. Lives only here. |
| `BRIGHTDATA_LINKEDIN_DATASET_ID` | no | `gd_l1viktl72bvl7bjuj0` | The LinkedIn profile dataset, same as `config.py` in the app. |
| `KV_REST_API_URL` | yes | | Set by the Vercel KV / Upstash integration. `UPSTASH_REDIS_REST_URL` also works. |
| `KV_REST_API_TOKEN` | yes | | Set by the same integration. `UPSTASH_REDIS_REST_TOKEN` also works. |
| `ART_GATEWAY_DAILY_QUOTA` | no | `3` | Profiles per GitHub user per UTC day. |
| `ART_GATEWAY_MONTHLY_CAP_USD` | no | `20` | Spend cap for the calendar month (UTC). |
| `ART_GATEWAY_COST_PER_RECORD_USD` | no | `0.0015` | What one record costs. Calls times this is the spend. **Check Bright Data's current price and set this.** |
| `ART_GATEWAY_IP_PER_MINUTE` | no | `10` | Requests per IP per minute. |
| `ART_GATEWAY_AUTH_CACHE_SECONDS` | no | `300` | How long a GitHub token check is remembered. |
| `ART_GATEWAY_UPSTREAM_TIMEOUT` | no | `100` | Seconds to wait for Bright Data. Keep it under the function's `maxDuration` (120, in `vercel.json`). |
| `ART_GATEWAY_GITHUB_TIMEOUT` | no | `8` | Seconds to wait for GitHub. |
| `ART_GATEWAY_ALLOW_MEMORY_STORE` | no | | `1` uses in-process counters when no KV is set. Local development only; never set it on Vercel. |

An unusable value (say `ART_GATEWAY_DAILY_QUOTA=three`) makes the service answer `503 not_configured` and log the variable name, rather than quietly using a default.

## Local development

Needs Python 3.12. No install step: the service has no dependencies.

```bash
cd gateway
pip install -r requirements-dev.txt     # pytest, for the tests only
python -m pytest                        # hermetic: fakes for GitHub, Bright Data and KV

# Run it. This calls the real GitHub and Bright Data, so use a real token and key.
BRIGHTDATA_API_KEY=... ART_GATEWAY_ALLOW_MEMORY_STORE=1 python -m artie_gateway.devserver
curl -s localhost:3000/v1/linkedin -H "Authorization: Bearer $(gh auth token)" \
  -H 'Content-Type: application/json' -d '{"url": "https://www.linkedin.com/in/<your-name>"}'
```

Fetch your own profile only (LinkedIn's terms prohibit scraping other people's).

## Deploy to Vercel (owner steps)

Vercel Hobby is non-commercial. If ARTie ever charges, move this project to Pro.

1. **New project.** In the Vercel dashboard choose Add New, then Project, and import `nathansso/agentic_resume_tailoring`. Name it `artie-gateway`.
2. **Root Directory:** `gateway`. Framework Preset: **Other**. Leave Build and Output settings empty. Python is picked up from `.python-version` (3.12).
3. **Storage.** Open the project's Storage tab, choose Create Database, and pick Upstash for Redis (the Vercel KV replacement in the Marketplace). Connect it to this project for Production and Preview. That adds `KV_REST_API_URL` and `KV_REST_API_TOKEN`. Use a **separate** database from anything else.
4. **Environment variables** (Settings, Environment Variables), for Production, and for Preview only if you want previews to work:
   - `BRIGHTDATA_API_KEY` (mark Sensitive)
   - `BRIGHTDATA_LINKEDIN_DATASET_ID` (optional)
   - `ART_GATEWAY_COST_PER_RECORD_USD` (from Bright Data's price list)
   - `ART_GATEWAY_MONTHLY_CAP_USD` and `ART_GATEWAY_DAILY_QUOTA` (optional)
   - **Do not** set `ART_GATEWAY_ALLOW_MEMORY_STORE`.
5. **Function duration.** Bright Data's synchronous scrape can take over a minute. `vercel.json` sets `maxDuration` to 120 seconds, which needs Fluid Compute (on by default for new projects; Settings, Functions to confirm).
6. **Deploy.** Push to `main` (Git integration) or press Deploy.
7. **Check it** (use your own profile and a real GitHub token):
   ```bash
   curl -i https://<deployment>.vercel.app/v1/linkedin -d '{}'                        # 401
   curl -i https://<deployment>.vercel.app/v1/linkedin -H "Authorization: Bearer $(gh auth token)" \
     -H 'Content-Type: application/json' -d '{"url": "https://www.linkedin.com/in/<your-name>"}'   # 200
   ```
8. Tell the harness client (#355) the production URL. It is a constant there, not user configuration.

To switch the endpoint off in an emergency, set `ART_GATEWAY_MONTHLY_CAP_USD=0` and redeploy; every call then returns 503.

## Layout

```
gateway/
  api/v1/linkedin.py      Vercel function: the `handler` class
  artie_gateway/
    service.py            the request pipeline (no HTTP in it)
    adapter.py            BaseHTTPRequestHandler wrapper
    urls.py               profile URL validation
    github.py             token check, user id, short cache
    brightdata.py         the scrape call, ported from ingestion/linkedin.py
    store.py              counters: InMemoryStore, UpstashStore
    transport.py          the only network code (urllib); tests swap it
    config.py             environment settings
    devserver.py          local run
  tests/                  hermetic; run by .github/workflows/gateway.yml
  vercel.json             /v1/linkedin rewrite, maxDuration
```

Why Python: the request logic is a port of `ingestion/linkedin.py`, `tests/test_gateway_contract.py` in the main suite keeps the two in step, and the whole service needs no third-party package.
