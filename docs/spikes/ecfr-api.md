# Spike E11-T3: eCFR terms page and API limits

Read and probed on 2026-11-21 (the server clock showed Sun 22 Nov 2026 02:45 GMT). Base URL:
`https://www.ecfr.gov/api/versioner/v1`. No key. The probe is `backend/scripts/spike_ecfr_api.py`
(7 requests, 1 per second, User-Agent `FinAdvisor-spike/0.1 (eCFR API probe; research prototype)`,
no personal data). The samples it recorded are in `backend/tests/fixtures/ecfr/`.

## Summary

| Question | Arch says | Live result |
|---|---|---|
| Terms page wording on automated access and reuse | UNVERIFIED | Not readable: the page sits behind a CAPTCHA (see Terms). No wording on rate limits or reuse retrieved. |
| Vendor rate limit | UNVERIFIED, 1 rps | None published in any response header; docs page not readable. Keep 1 rps. |
| Request without `Accept-Encoding: gzip` | 406 | 200. gzip is not required. |
| `versions/title-17.json?part=275` records | 143 | 145 |
| Removed (`removed: true`) | 9 | 9 |
| `substantive: false` | not stated | 28 |
| `titles.json` title 17 `up_to_date_as_of` | field exists | `2026-11-19` |
| Point-in-time `/full/{date}/` | exists | 200, 225,615 bytes decoded, 39 sections |

## Terms page

URLs tried with a web fetch tool:

- `https://www.ecfr.gov/reader-aids/using-ecfr/ecfr-terms-and-conditions` (terms)
- `https://www.ecfr.gov/developers/documentation/api/v1` (developer documentation)

Both answered `302 Found` to `https://unblock.federalregister.gov/`, a Federal Register "Request
Access" CAPTCHA page. A plain `curl` of the same pages got the same page with HTTP 200. The CAPTCHA
was not solved and not bypassed. The page wording relevant to us (quoted):

> "Due to aggressive automated scraping of FederalRegister.gov and eCFR.gov, programmatic access to
> these sites is limited to access to our extensive developer APIs."

> "Please visit FederalRegister.gov API documentation or eCFR.gov API documentation to learn more
> about how to access the API."

> "Your request has been flagged as potentially automated."

Result for the acceptance criterion: the terms and the API documentation could not be read, so the
terms wording on automated access and reuse (and any published rate limit) is NOT retrieved. What
the official site does say, on the page that blocks us, is that programmatic access is limited to the
developer APIs. That is consistent with the arch: the connector uses only `/api/versioner/v1/...`
and never scrapes `www.ecfr.gov` HTML pages. It must never fetch `reader-aids` or other site pages.

Finding for the orchestrator (a question, not a block): someone with a browser should read the terms
page and the API documentation once and confirm (a) any published limit and (b) the reuse wording,
before E11-T15 ships to production. Until then the connector keeps the arch's 1 rps and the
"unofficial compilation, not authentic" label (arch 08-security, `us_gov`).

## Accept-Encoding

All requests got HTTP 200. The status is the same with and without the header.

| Endpoint | `Accept-Encoding: gzip` | `identity` | header omitted |
|---|---|---|---|
| `titles.json` | 200, `content-encoding: gzip`, 8,033 bytes decoded | 200, no content-encoding | 200, no content-encoding |
| `versions/title-17.json?part=275` | 200, `content-encoding: gzip`, 39,745 bytes decoded | 200, no content-encoding | 200, no content-encoding |
| `full/2026-11-19/title-17.xml?part=275` | 200, `content-encoding: gzip`, 225,615 bytes decoded | not probed | not probed |

The probe client removes httpx's own default `Accept-Encoding`, so "header omitted" really sends none.
The arch's "406 without gzip" was not reproduced (an earlier curl probe agreed). Finding: the
connector should still send `Accept-Encoding: gzip` (smaller transfers; the arch asks for it), but must not treat a missing header as an error path to test.
The test `test_ecfr_request_without_gzip_is_not_sent` is not needed.

## Rate limit

No `X-RateLimit-*`, `RateLimit-*` or `Retry-After` header on any of the 7 responses (the script
records any header starting with those names; none appeared). The API documentation, where a limit
would be stated, was not readable. Decision: keep 1 request per second (the arch figure) as the
config knob `CONNECTOR_RATE_RPS_ECFR` (`Settings.ecfr_rate_limit_rps`, float, default 1.0, must be
greater than 0, `.env.example` documents it). Seven requests at 1 rps drew no throttling response.
This confirms nothing about higher rates, so the value stays conservative.

## `titles.json`

Keys: `titles` (50 entries), `meta` (`date` 2026-11-19, `import_in_progress` false). Title 17:
`name` "Commodity and Securities Exchanges", `latest_amended_on` 2026-11-06,
`latest_issue_date` 2026-11-16, `up_to_date_as_of` 2026-11-19, `reserved` false.
`up_to_date_as_of` is the field the arch watches: when it advances, diff the versions list.

## `versions/title-17.json?part=275`

Keys: `content_versions`, `meta` (`result_count` "145", `latest_amendment_date` 2026-11-06,
`latest_issue_date` 2026-11-06). Each record: `date`, `amendment_date`, `issue_date`, `identifier`,
`name`, `part`, `substantive`, `removed`, `subpart`, `title`, `type`. All 145 records are
`type: section`, `subpart: null`; `date` equals `amendment_date` on every record. The first record
is `275.0-2`, 2016-12-31.

| Count | Arch | Live |
|---|---|---|
| Records | 143 | 145 (drift of 2: new amendments after the arch was written) |
| Distinct identifiers | not stated | 48 |
| `removed: true` | 9 | 9 (all distinct identifiers) |
| `substantive: false` | not stated | 28 (none of them also removed) |

- The diff key `(identifier, amendment_date, issue_date)` is unique over all 145 records (tested in
  `test_version_key_is_unique_per_record`).
- `removed: true` records are the tombstones. The 9 are `275.206(4)-3`, `275.206(4)-4` (2021-04-09),
  `275.206(4)-9`, `275.206(4)-10`, `275.211(h)(1)-1`, `275.211(h)(1)-2`, `275.211(h)(2)-1`,
  `275.211(h)(2)-2`, `275.211(h)(2)-3` (2024-11-19). A removed record keeps its own
  `amendment_date`, so a tombstone arrives as a record, not as a missing one.
- `substantive: false` marks metadata-only changes (arch 06 6.7): the connector records them but does
  not re-ingest text for them.
- The count drifts, so the connector must not hard-code 143. The quality rule "eCFR part has the
  expected section ids" should compare against the identifiers listed, not a fixed total.

## Point-in-time `/full/{date}/`

`GET /api/versioner/v1/full/2026-11-19/title-17.xml?part=275` (date = title 17 `up_to_date_as_of`)
returned 200 with XML, 225,615 bytes decoded (about 51 KB over the wire with gzip), containing 39
elements `TYPE="SECTION"`, which equals the 48 listed identifiers minus the 9 removed ones. The sample is not stored as a fixture (it is large and
the connector test can record it later); its sha256 is
`2306692121f2423a5d4645f002f2da417bd7af6cfd9b6b00fd3088e8f7f38b0f`.
Other response facts (curl probe): HTTP/2, `cache-control: no-store`, `vary: Origin, Accept-Encoding`.

## Findings for the orchestrator

1. Terms page and API documentation are not readable without solving a CAPTCHA, which was not
   attempted. A human needs to read them once (question: any published limit, any reuse
   restriction). The CAPTCHA page itself says programmatic access is limited to the developer APIs.
2. Arch drift to update: 406 without gzip is wrong (200); 145 versions, not 143; 9 removed is right.
3. Rate knob added with the arch's 1 rps; unchanged until the terms are read.
4. Nothing found that forbids what the arch assumes.
