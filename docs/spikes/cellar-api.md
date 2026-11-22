# Spike E11-T2: CELLAR rate behaviour and formats

Probed on 2026-11-22 (two runs, the second after the machine slept; server clock GMT). Endpoints, no key:

- SPARQL: `https://publications.europa.eu/webapi/rdf/sparql`
- REST: `https://publications.europa.eu/resource/celex/<CELEX>` (content negotiation, answers `303`)

The probe is `backend/scripts/spike_cellar_api.py`: 31 requests per run, at most 1 per second (every hop of a
`303` chain goes through the throttle), User-Agent `FinAdvisor-spike/0.1 (CELLAR API probe; research prototype)`,
no personal data. The samples it recorded are in `backend/tests/fixtures/cellar/`.

Run it by hand, never in the PR gate, as a module from `backend/` (running the file directly fails because
`src` is not importable):

    cd backend && python -m scripts.spike_cellar_api --out-dir tests/fixtures/cellar

The rate defaults to `CONNECTOR_RATE_RPS_EURLEX` (`Settings.eurlex_rate_limit_rps`, default 1.0). A higher
`--rps` is refused unless `--allow-above-limit` is also given. A burst is above 1 rps by definition, so
`--burst N` (N tiny SPARQL queries sent at once) is refused without `--allow-above-limit` too. Both runs here used
`--burst 5 --allow-above-limit` and the default 1 rps for everything else.

## Config name

The ticket asks for `EURLEX_RATE_LIMIT_RPS`. The setting is `eurlex_rate_limit_rps` and its environment variable
is `CONNECTOR_RATE_RPS_EURLEX`, the pattern of arch deep-dive/06 section 6.7 (`CONNECTOR_RATE_RPS_<SOURCE>`) and of
the eCFR sibling (`CONNECTOR_RATE_RPS_ECFR`). `EURLEX_RATE_LIMIT_RPS` is not read. Default 1.0, `> 0`, listed in
`.env.example`; test `test_eurlex_rate_limit_config_present`.

The URLs, User-Agent, timeout, acts and languages in the script are spike-local constants, not knobs. The connector
ticket (E11-T18) moves the URLs and timeout into Settings as EURLEX knobs, together with the throttle.

## Summary

| Question | Arch says | Live result |
|---|---|---|
| Rate limit | undocumented (UNVERIFIED), 1 rps | None seen. No `429` or `503`, no `Retry-After` or `X-RateLimit-*` header in any response, at 1 rps and in a burst of 5. Keep 1 rps. |
| Consolidated versions per language | 43 (14, 11, 5, 6, 7) | 43 (14, 11, 5, 6, 7). No difference. |
| Languages | EN, DE, FR | All three on 42 of 43 versions; one version has no English (see below). 23 or 24 languages per act in all. |
| Content types, consolidated CELEX | XHTML and Formex, no `text/html`, no `pdf` | Confirmed: XHTML `200`, Formex `200` (zip), `text/html` `404`, `application/pdf` `404`. |
| Poll property | `cdm:lastModificationDate` | The property is `cmr:lastModificationDate`, namespace `http://publications.europa.eu/ontology/cdm/cmr#`, not `cdm:`. |
| Corrigenda | separate CELEX numbers | Yes, and each exists only in the languages it corrects (see Corrigendum). |

## Consolidated versions per act

Found with `cdm:resource_legal_id_celex` and `STRSTARTS(STR(?celex), "0<year><type><number>-")`. Consolidated CELEX
form: `0YYYYTNNNN-YYYYMMDD` (the date is the date the version applies from). Counts and the EN/DE/FR columns are
versions that have an expression in that language.

| Act | CELEX stem | Found | Arch | Difference | EN | DE | FR |
|---|---|---|---|---|---|---|---|
| MiFID II (Directive 2014/65/EU) | `02014L0065-` | 14 | 14 | 0 | 14 | 14 | 14 |
| MiFIR (Regulation (EU) No 600/2014) | `02014R0600-` | 11 | 11 | 0 | 11 | 11 | 11 |
| PRIIPs (Regulation (EU) No 1286/2014) | `02014R1286-` | 5 | 5 | 0 | 5 | 5 | 5 |
| Delegated Regulation (EU) 2017/565 | `02017R0565-` | 6 | 6 | 0 | 5 | 6 | 6 |
| RTS, Delegated Regulation (EU) 2017/653 | `02017R0653-` | 7 | 7 | 0 | 7 | 7 | 7 |
| Total | | 43 | 43 | 0 | 42 | 43 | 43 |

Versions found (date suffixes):

- MiFID II: 20140917, 20160701, 20181001, 20191231, 20200326, 20210626, 20211110, 20220101, 20220228, 20230323,
  20240109, 20240328, 20250117, 20260606
- MiFIR: 20140702, 20160701, 20200326, 20200704, 20210626, 20220101, 20240109, 20240328, 20241204, 20250117,
  20251123
- PRIIPs: 20141229, 20161224, 20190801, 20211221, 20240109
- Delegated Reg 2017/565: 20170331, 20171214, 20191011, 20210329, 20210822, 20220802
- RTS 2017/653: 20170412, 20180801, 20191128, 20220101, 20220714, 20221019, 20230101

The arch figure of 43 holds today. It is a snapshot: MiFID II got a new version dated 20260606, so the count grows
as consolidation catches up. The connector must list versions, not hard-code 43.

Language finding: `02017R0565-20170331` (the first consolidated version of Delegated Regulation 2017/565) has no
English expression, only other languages. The connector must not assume a version exists in all three languages;
the languages query (`cdm:expression_belongs_to_work` and `cdm:expression_uses_language`, codes from
`.../authority/language/<ENG|DEU|FRA>`) answers per version.

## Observed behaviour at 1 rps and a short burst

At 1 rps, 31 requests per run (SPARQL and REST hops):

| Run | Status codes seen | Notes |
|---|---|---|
| 1 | `200` for every SPARQL query and every XHTML and Formex request; `404` for `text/html` and `application/pdf` | No failures. |
| 2 | Same, plus two network failures (status 0) | `ReadTimeout` after 60 s on one SPARQL versions query (`versions_dr_2017_565`); `ReadError` after 18 s while downloading a corrigendum Formex zip. The same two requests worked in run 1 and by hand. |

No `429`, no `503`, no rate-limit header in either run. The two failures are slow or dropped connections, not
throttling. Run 2 is the one kept in `probes.json` (the run 1 statuses are summarised here from its log); the
`sparql_versions_dr_2017_565.json` sample is from run 1, since run 2 timed out on that query.

Short burst: 5 tiny SPARQL queries (`LIMIT 1`) sent at once, in each run. All five answered `200`
(0.11 to 0.56 s each in run 1, 0.45 to 0.47 s in run 2). A burst of 5 is not a stress test; a limit above 5 requests
at once is not excluded. Nothing here says the limit is higher than the arch's 1 rps either, so the knob stays at
1.0 and the finding is "no throttling observed up to a burst of 5".

Latency: SPARQL queries took 3.6 to 10 s (one 26.7 s); REST hops 0.1 to 0.4 s. A connector timeout under 60 s
would have cut off run 2's slow query. The spike does no retries, on purpose, to show the raw behaviour; the
connector needs a timeout of at least 60 s, bounded retries with backoff and jitter, and idempotent re-listing.

## SPARQL notes

- Exact CELEX match needs the explicit datatype: `?w cdm:resource_legal_id_celex "02014L0065-20250117"^^xsd:string`.
  Comparing a plain literal (`FILTER(?celex = "...")`) returned no rows. `STRSTARTS(STR(?celex), "...")` works.
- A `REGEX` with `|` alternatives returned no rows. Use one `STRSTARTS` per act joined with `||`.
- Poll query (recorded in `sparql_poll.json`, 33 rows since 2026-01-01 for the five acts), shape:
  `?w cdm:resource_legal_id_celex ?celex . ?w cmr:lastModificationDate ?lm . FILTER(STRSTARTS(...)) FILTER(?lm >= "<since>"^^xsd:dateTime) ORDER BY ?lm`
  with `PREFIX cmr: <http://publications.europa.eu/ontology/cdm/cmr#>`.
- `lastModificationDate` is not a content-change date. 29 of the 43 consolidated versions share one
  modification date, 2026-02-19 (a bulk re-touch of old versions); the dates are written with a time zone
  (`2026-02-19T08:59:16.658+01:00`). A poll on this property alone would report old versions as changed. The
  connector must compare a content hash before it treats a version as new (arch 4.15 does hash content).
- Each act's list query and language query took 3.6 to 10 s; the language query returns one row per version and language (23 or 24 per version).

## REST content negotiation (latest consolidated MiFIR, `02014R0600-20251123`)

`GET /resource/celex/<CELEX>` with `Accept` and `Accept-Language` answers `303` with a `Location` on the same
host, then the document. The `Location` is `http://`, so the next hop is made on `https://`. Each document costs two
requests. Both count against the 1 rps.

| Accept | Language | Result |
|---|---|---|
| `application/xhtml+xml` | `eng` | `303` then `200`, `application/xhtml+xml;charset=UTF-8`, 721,056 bytes |
| `application/xhtml+xml` | `deu` | `303` then `200`, 758,102 bytes |
| `application/xhtml+xml` | `fra` | `303` then `200`, 804,258 bytes |
| `application/zip;mtype=fmx4` | `eng` | `303` then `200`, `application/zip`, 82,875 bytes: two files, `CL2014R0600EN0100010.0001.xml` and `...0001.doc.xml` |
| `text/html` | `eng` | `404` (214 bytes) |
| `application/pdf` | `eng` | `404` (214 bytes) |

Also seen by hand: `application/xml;notice=object` returns the object metadata notice (`200`, small);
plain `application/xml` returns `404`. The metadata lists a `pdfa2a` manifestation for the 2025 MiFID II version,
but `application/pdf` negotiation returned `404`; other PDF Accept values were not tried. The arch statement (no
`text/html`, no `pdf`) holds for the Accept values the connector would use.

The XHTML starts with a `CONVEX` comment giving converter version and generation time
(`generated_on:20260211-0035`); that time can serve as a version stamp in addition to the content hash.

## Corrigendum

A corrigendum is a separate work with its own CELEX: `32014R0600R(01)` to `R(11)` for MiFIR, 19, 11, 8, 9 and 8
for MiFID II, MiFIR, PRIIPs, 2017/565 and 2017/653 (`sparql_corrigenda_<act>.json`). Findings:

- `32014R0600R(01)` is not resolvable through the REST `celex` alias: `404` "Resource [system 'celex' - id
  '32014R0600R(01)'] not found", for every Accept value tried (XHTML, Formex, `text/html`, pdf, `*/*`) and with
  the parentheses percent-encoded. One by-hand `text/html` attempt hung until the 60 s client timeout.
- The CELLAR work IRI from the SPARQL answer works: `https://publications.europa.eu/resource/cellar/fe1d3742-760c-11e4-b593-01aa75ed71a1`.
- The corrigendum exists in French only (`sparql_corrigendum_languages.json`). Asked in English, the work IRI
  answers `404` (XHTML) and `406` (Formex). Asked in French: XHTML `200` (2,463 bytes) and Formex zip `200` (2,116
  bytes by hand). Its manifestation types are `fmx4`, `xhtml`, `pdfa1a`.
- So the connector cannot take the language list of the base act for a corrigendum; it reads the languages per
  corrigendum, and fetches the corrigendum by work IRI.

## Recorded samples

All in `backend/tests/fixtures/cellar/`, loaded by `test_cellar_samples_load`:

- `sparql_versions_<act>.json` (raw SPARQL JSON, celex, work and modification date), `sparql_languages_<act>.json`
  (a compact `{celex: [language codes]}` map, trimmed from 115 to 322 raw rows per act)
- `sparql_poll.json`, `sparql_corrigenda_<act>.json`, `sparql_corrigendum_languages.json` (raw SPARQL JSON)
- `rest_samples.json` (first 1,500 characters of the XHTML in three languages and of the corrigendum, the Formex
  zip member names)
- `probes.json` (every probe with each hop's status and time, and the status count), `burst.json`

## Open items for the connector (E11-T18)

- Timeout of at least 60 s for SPARQL, retries with backoff and jitter on timeouts and read errors.
- Use `cmr:lastModificationDate` as a hint only, then compare a content hash.
- List versions and languages per act and per corrigendum; do not assume 43 or three languages.
- Follow the `303` with an `https` second hop; both hops count against `CONNECTOR_RATE_RPS_EURLEX`.
- A vendor limit is still not published. Someone should confirm the Publications Office terms and any documented
  limit before production; until then 1 rps and no burst.
