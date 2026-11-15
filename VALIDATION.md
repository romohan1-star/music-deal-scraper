# Initial validation — November 15, 2026

This is a functioning release with a **resumable inventory checkpoint**, not a
claim that the requested retailer universe has been completely collected.

## Verified results

- 20 automated tests passed locally.
- 10,774 unique retailer offers in the local SQLite snapshot and inventory CSV.
- Excel All Offers reconciled to all 10,774 SQLite observations, including prices.
- SQLite integrity check passed; no formulas were present in the exported data.
- Seven workbook sheets generated, including the six requested sheets plus
  Validation Evidence. The personal candidate sheet contains 30 rows.
- Sources: ProAudioStar 9,613 offers; Pitbull Audio 791; Focus Pro Audio 370.
- 40 known collections marked VERIFIED COMPLETE; 421 listing-page attempts in
  the checkpoint. This is not a retailer-wide completeness claim.
- 127 saved random product detail samples were rechecked without new requests.
  Across all latest collection-count, boundary and product checks: 221 PASS and
  one FAIL. The failure remains visible in Errors and Limitations.
- No reliable cross-retailer matches were established. Identity/condition fields
  are too sparse to merge uncertain products. The comparisons sheet says so.
- No AI calls are used by collection, processing, ranking or exports.

## Cloud delivery verification

[Live smoke validation run](https://github.com/romohan1-star/music-deal-scraper/actions/runs/43689328125)
completed successfully: tests, real inventory collection, CSV/Excel/SQLite
reconciliation, nonzero-inventory assertion, summary and artifact upload all passed.
Artifact `14151258723` was listed as unexpired and its download returned a reusable
ZIP reference. This bounded test is not the full crawl.

[Full collection run](https://github.com/romohan1-star/music-deal-scraper/actions/runs/43686484044)
was still running when the interactive snapshot was finalized. Its eventual
outcome must be read from GitHub; this document does not assert success or upload.
That initial run started from commit `f06b369`. Later hardening was committed to
main; future manual runs use the latest code. Recurring scheduling stays disabled.

## Exact remaining scope

- All three Tier 1 stores returned access denials; their parsing/pagination
  adapters are not live-certified. They currently provide conservative JSON-LD
  fallback only and require permitted source access before fuller implementation.
- AMS, B&H and Perfect Circuit were access-blocked in this environment.
- zZounds, Alto Music, CME, Vintage King, Kraft and The Music Zoo were stopped by
  the reviewed terms policy. Written permission or an authorized feed is needed.
- Focus rate limits blocked additional categories. Its main endpoint exposed 364
  products against an explicit metadata count of 370; six further unique offers
  were recovered from another collection. Neither fact proves main-collection or
  retailer-wide completeness.
- Several large ProAudioStar collections had duplicate IDs, changing counts or
  an unexpected empty pagination response. They remain PARTIAL.
- The interactive snapshot retains 58 discovered but untraversed collections,
  primarily nested ProAudioStar collections, as PARTIAL. Resume the run ID
  `validation-20261115` with the saved database, evidence and original config.
- Coupon eligibility/application, browser-only listings, additional unnamed
  retailers and exhaustive discovery of unlinked campaigns remain unimplemented
  or unverified. Missing values were not fabricated.
- Only one observation run exists in this local checkpoint; it cannot establish
  historical selling prices or historical lows.

The snapshot's machine-readable coverage has 40 VERIFIED COMPLETE, 64 PARTIAL,
35 BLOCKED and 11 UNVERIFIABLE collection rows. These are collection counts, not
retailer counts. All 15 named retailers have explicit status records.

Artifacts expire after 30 days or the repository's shorter retention policy.
Download the data/evidence snapshot to retain or resume it beyond that period.
