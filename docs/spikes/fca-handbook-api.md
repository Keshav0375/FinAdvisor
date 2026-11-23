# Spike E11-T1: FCA Handbook API format and rate limit

Status: DEMO MODE. There is no FCA Handbook API account yet (K7), so nothing below about the
response format, the rate limit, the past-date status or the login errors was observed on the real
API. It is what the demo run reports, from fixtures built from the public FCA description. Every
such fixture is marked `demo fixture, from docs` (key `_demo` in the file). The real values are
filled in by running the command under "Confirm with the real account (K7)".

The probe is `backend/scripts/spike_fca_api.py`. Run it as a module from `backend/` (running the
file directly fails because `src` is not importable):

    cd backend && python -m scripts.spike_fca_api

Demo is the default (`FCA_API_MODE=demo`, `FCA_API_KEY=demo-fca-key`, both read by the script's own
`FcaSpikeSettings`, not by the app). It needs no account and no network: an in-process transport
replays `backend/tests/fixtures/fca_api/`. It prints one JSON report on stdout (request logs go to
stderr). Exit codes: 0 done, 1 transport error, 2 bad configuration (for example `live` with the
demo key, or a live `--base-url` that is not an `fca.org.uk` host).

## What the public FCA pages say (read 2026-11-21)

Only this is published where it can be read without a login:

- FCA blog "Making compliance simpler: opening up the FCA Handbook through our new API" (6 Aug
  2026): the Handbook is available "in a structured, machine-readable format"; access to "current
  and future versions of Handbook content".
- The Handbook API page (`handbook.fca.org.uk/handbook-api`) is "sign in required"; the API is
  free; it is used through a tool such as Postman or RapidAPI, not the website.

NOT published (the pages that would say are behind the login or did not render for a fetch):
endpoint paths, JSON field names, the auth header name, any rate number, the status for a past
date. So the JSON shape, the paths and the header in the script are assumptions, marked
`ASSUMPTION` in the code.

## Findings (demo run, not observed)

| Question | Arch says | Demo run reports (source: demo fixture, from docs) | Real value |
|---|---|---|---|
| Response format | JSON "not seen" | `application/json`; keys `reference`, `title`, `version_date`, `last_modified`, `provisions[]` (`reference`, `type`, `text`) | confirm (K7) |
| `Last modified` signal | "Last modified" column | body field `last_modified` and header `Last-Modified` both present | confirm which exists (K7) |
| Past date | current and future only, an error | `400` with an error body | confirm the code (K7) |
| Revoked login | suspend on 401 or 403 | `401` | confirm (K7) |
| Suspended login | suspend on 401 or 403 | `403` | confirm (K7) |
| Rate limit | per authenticated user and endpoint path, no number | none reached after 10 requests at 1.0 rps (demo transport never throttles) | confirm (K7) |

The demo numbers prove the script and the report end to end. They say nothing about the FCA.

## Rate limit: `FCA_API_RATE_LIMIT_RPS`

`Settings.fca_api_rate_limit_rps` (float, default 1.0, must be greater than 0, documented in
`.env.example`). Justification: the FCA publishes no number, and the arch says only "rate-limited per
authenticated user and endpoint path"; 1 request per second is the same conservative figure the
other connectors use (eCFR, EUR-Lex, legislation.gov.uk). It is the cap for the spike: `--rps`
above it is refused unless `--allow-above-limit` is given. Keep 1.0 until the real run (below)
shows a 429 sooner or a published limit is read. The tests `test_fca_rate_limit_config_present`
and `TestFcaApiRateLimit` fail if the key is missing from `Settings` or `.env.example`.

This is the only FCA knob in the global `Settings`, so app boot never depends on FCA config. The
rest is spike-local, in the script's `FcaSpikeSettings` (env prefix `FCA_API_`):

| Variable | Default | Meaning |
|---|---|---|
| `FCA_API_MODE` | `demo` | `demo` replays fixtures; `live` calls the API |
| `FCA_API_KEY` | `demo-fca-key` | account key; `live` rejects the demo key, a blank one and placeholders |
| `FCA_API_TIMEOUT_S` | 30 | request timeout, seconds |
| `FCA_API_BURST_REQUESTS` | 10 | repeat reads for the rate check |
| `FCA_API_PAST_DATE_OFFSET_DAYS` | 30 | past-date probe: days before today |
| `FCA_API_BASE_URL` | none | live base URL (or `--base-url`) |
| `FCA_API_RATE_LIMIT_RPS` | 1.0 | the global knob above; `--rps` is capped by it |

The endpoint path, date parameter and auth header default are spike-local constants. The connector
(E11-T20) owns the mode, key, timeout, burst and base URL and moves them into its own config.

A live `--base-url` must be https, carry no credentials and be an `fca.org.uk` host (the Handbook
API page is on `handbook.fca.org.uk`), because the key is sent there. `--allow-any-host` overrides
that on purpose.

## Fixtures

`backend/tests/fixtures/fca_api/` (PUBLIC fixtures folder only): `cobs_9a_module.json`,
`cobs_6_module.json`, `past_date_error.json`, `auth_error_401.json`, `auth_error_403.json`. The
module text is a placeholder ("not FCA text"), so no FCA text is stored: the licence texts conflict
(arch ADR-026: cl.35.3, cl.3.3) and cl.13 requires deleting copies on revocation. The loader is
`spike.load_sample` (refuses a file without the demo marker); `test_sample_loads` covers it.

When the real run records samples, keep to the minimum FCA text needed, keep them only here, and
delete them if the FCA revokes the login.

## Confirm with the real account (K7)

1. Create a free account on the Handbook API page and read its documentation for the base URL,
   the module path, the date parameter and the auth header name.
2. Run, from `backend/` (the key goes in the environment, never on the command line):

        FCA_API_MODE=live FCA_API_KEY=<your account key> python -m scripts.spike_fca_api \
            --base-url <API base URL from the Handbook API page> \
            --module-path <module path, with {sourcebook} and {chapter}> \
            --auth-header <auth header name> \
            --out-dir tests/fixtures/fca_api/recorded

   It sends 14 requests at most `FCA_API_RATE_LIMIT_RPS` per second (4 fixed plus the burst), stops
   the burst at the first 429, and writes the COBS 9A and COBS 6 responses it got into `--out-dir`. If the date
   parameter is not `date`, change `DATE_PARAM` in the script.
3. Copy into this note: `format` (content type, keys, `parsed`), `last_modified_header`,
   `past_date.status`, `auth` and `rate.statement`. `parsed: false` means the real shape differs
   from the assumed one; then fix `ModuleResponse` and the fixtures to the recorded shape.
4. Replace the demo fixtures with trimmed recorded ones (minimum text), drop the demo marker from
   the real ones, and remove "DEMO MODE" from this note.

A live run cannot make the FCA revoke or suspend a login. The live report has one `auth` row,
`unknown_key`, labelled "unknown-key response (no revoked key available)": it is what an unknown
key gets, not a real revoked 401 or suspended 403. The code for a truly revoked login has to be
read from the real connector later (kill switch: suspend on 401 or 403, `purge_source('fca_api')`).
The `revoked` and `suspended` rows exist in demo mode only.

## Findings for the orchestrator

1. Needs Keshav (K7): the account and one live run. Until then the format, rate and error codes are
   unverified, and the arch's "UNVERIFIED" rows stay so.
2. ADR addendum is the orchestrator's: record that the rate knob is `FCA_API_RATE_LIMIT_RPS`, 1.0,
   from the arch's conservative figure, not from an observation.
3. Global config added: only `FCA_API_RATE_LIMIT_RPS`; it sits next to the eCFR knob added in
   E11-T3 (adjacent lines in `config.py` and `.env.example`). E11-T20 owns the mode, key and the
   other FCA knobs.
