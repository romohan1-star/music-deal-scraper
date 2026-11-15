# Measured validation — November 15, 2026

This release runs unattended and produces actual inventory. It does **not** cover
all requested retailers or establish whole-retailer completeness.

## Verified results

- 23 automated tests pass locally. Cloud report refresh also ran the tests.
- Completed collection run `20261115T064019Z`: **12,703 unique retailer offers**.
  Focus Pro Audio: 1,521; Pitbull Audio: 791; ProAudioStar: 10,391.
- CSV and Excel All Offers reconcile to all 12,703 SQLite observations and prices.
  SQLite integrity passes and the workbook contains no executable formulas.
- Seven workbook sheets include the six requested sheets and Validation Evidence.
  The Best Deals sheet contains 30 deterministic personal review candidates.
- Coverage: **97 VERIFIED COMPLETE, 25 PARTIAL, 25 BLOCKED, 24 UNVERIFIABLE**
  collection records. These are collection counts, not retailer counts.
  Complete collections: Pitbull 12; ProAudioStar 85. Focus's denominators remain
  unverified. All 15 named retailers have explicit status records.
- 332 saved random product samples were rechecked with current parsing rules,
  without new network requests. Latest count, boundary and product checks total
  **556 PASS and 3 FAIL**; failures remain visible in the reports.
- The failures concern unstable product IDs on ProAudioStar drums and
  guitars/basses boundary pages. Those collections remain PARTIAL, with counts
  947/1,068 and 5,430/5,510 respectively.
- No defensible cross-retailer matches were established. All offers remain
  separate. Manufacturer model numbers and UPCs are missing in this snapshot;
  brand/SKU and condition are also sparse. No identifiers were guessed.
- No normal advertised retailer prices were reliably extracted. Compare-at
  values, where provided, are labeled unverified reference prices, not historical
  selling prices. This is an offer inventory, not proof of discount quality.
- No AI calls are used by collection, processing, ranking or exports.

## Cloud delivery verification

[Full collection run](https://github.com/romohan1-star/music-deal-scraper/actions/runs/43686484044)
completed successfully, including artifact upload. Artifact `14153205405`
contains 107,163,538 bytes; its downloaded ZIP SHA-256 was verified as
`b87724428a266e2bc355b7c20c66c0e1e8e9b79e30516c1c59155ddb76309b1c`.
It started from `f06b369`; subsequent parser, validation and ranking improvements
are on main. Stored evidence was re-audited before delivery.

[Live smoke validation](https://github.com/romohan1-star/music-deal-scraper/actions/runs/43689328125)
also succeeded: tests, live collection, nonzero-inventory assertion, output
reconciliation and artifact upload. This is a bounded test, not a full crawl.

[Saved-report refresh](https://github.com/romohan1-star/music-deal-scraper/actions/runs/43755647307)
succeeded: tests, restoration of the full snapshot, offline evidence audit,
report generation, reconciliation and artifact upload. Its artifact is
`music-deals-43755647307-1`. Further refreshes can incorporate ranking changes
without retailer requests. Refreshing reports never updates observed prices.

## Delivered data and history

The downloadable local delivery combines the completed cloud run with the earlier
`validation-20261115` checkpoint. Its Price History contains **23,477 observations
across two same-day runs**; current inventory still contains only the completed
run's 12,703 offers. The cloud-only artifact has one run because the earlier local
checkpoint was not uploaded into that job. Neither establishes historical lows.

Restore the delivery's `data/` alongside the GitHub checkout to retain both runs.
The original configuration, raw page evidence and logs accompany the database.
Do not run two processes against the same SQLite file. See README for fresh-run,
resume and export-only commands.

## Remaining scope and limitations

- Guitar Center, Sweetwater and Musician's Friend returned access denials. Their
  adapters offer only conservative JSON-LD fallbacks; full live extraction and
  pagination are not implemented/certified for them. Tier 1 remains incomplete.
- AMS, B&H and Perfect Circuit were access-blocked. zZounds, Alto Music, CME,
  Vintage King, Kraft and The Music Zoo were stopped under reviewed terms policy.
  Permitted access or an authorized feed is needed; controls were not bypassed.
- 25 ProAudioStar collections remain PARTIAL. Duplicates, count mismatches and
  unstable pagination prevent completion claims. Unknown counts remain unknown.
- Coupon eligibility/application, browser-only listings, additional unnamed
  retailers and exhaustive discovery of unlinked campaigns remain unimplemented
  or unverified. Used/open-box variants are retained separately when identifiable.
- Missing manufacturer identifiers, conditions, currency and availability reduce
  matching and recommendation confidence. All missing values and quality flags
  are preserved. Shipping and sales tax are not included.
- A rule-based shortlist is a review queue, not a conclusion that all 30 products
  add useful capabilities or represent good discounts. Verify fit and redundancy.
- GitHub Actions is the unattended runtime; no recurring Codex/LLM job is needed.
  Scheduling remains disabled initially. GitHub account runner/storage limits
  apply separately from ChatGPT Plus.

Artifacts expire after 30 days or the repository's shorter retention policy.
Download the database/evidence snapshot to retain or resume it beyond that period.
