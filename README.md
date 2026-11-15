# Music retailer promotion inventory

An ordinary Python program that collects promotional collections, preserves each
retailer offer, tracks what was and was not reached, and exports CSV/Excel/Markdown.
**No AI calls, OpenAI API key, paid inference service, or ChatGPT subscription is
required to run it.** GitHub Actions is the unattended runtime; Codex is only needed
when you want code changes or help investigating a broken collector.

## Current implementation and honest scope

The live validation results are in `reports/executive_summary.md` and
`reports/coverage.csv` inside the downloadable run artifact. Do not equate a green
workflow with complete retail coverage. A workflow can run correctly while a store
blocks access or a collection has no trustworthy advertised count.

Implemented live collection formats: Shopify public collection product JSON
(variant-aware, explicit empty-page termination), and Magento HTML product grids
(advertised counts, next links, explicit prices). Promotion landing pages discover
linked child collections. Configured retailers execute in tier order and fail in
isolation. The 15 requested retailers are configured, but **configuration is not
proof of support**: Tier 1 access was blocked during development, so its separately
named adapters currently have only a conservative JSON-LD fallback, not a verified
retailer-specific pagination implementation. They must be developed against
permitted live pages or authorized fixtures before being certified.

Terms-policy blocks are explicit in `config/retailers.json`. No browser stealth,
CAPTCHA solving, proxy rotation, authentication bypass, internal API probing, search
engine extraction, or paid feeds are used. Browser-only inventory is a documented
gap, not silently counted as complete. Additional unnamed retailers are not yet
automatically onboarded.

## Install and run locally (Python 3.11+)

```bash
git clone https://github.com/romohan1-star/music-deal-scraper.git
cd music-deal-scraper
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
python -m music_deals.cli collect
```

Windows activation: `.venv\Scripts\activate`. The runtime requires outbound HTTPS
and retailer permission. It prints the run ID before making collection requests.

Run selected stores or explicitly cap a development run:

```bash
python -m music_deals.cli collect --retailers focus,proaudiostar,pitbull
python -m music_deals.cli collect --retailers pitbull --max-pages 5
```

Caps produce PARTIAL coverage; they are never treated as successful exhaustion.
Default limits are 500 pages per collection and 100 collections per retailer.
At a minimum of 1.5 seconds between same-host requests, a broad run may take a while.
Robots crawl delay, recoverable retries and Retry-After are also respected. A 403 or
challenge stops the origin; repeated 429 stops it for the run. Retry later rather
than increasing concurrency or evading restrictions.

## Resume versus fresh history

```bash
python -m music_deals.cli collect --run-id THE_PRINTED_RUN_ID
python -m music_deals.cli export --run-id THE_PRINTED_RUN_ID
```

Resume requires the same JSON configuration and existing database/evidence. Pages
already completed are read from their saved evidence without refetching. Explicit
first/middle/final checks do re-request selected boundaries. Failed/unvisited pages
are attempted again. A page checkpoint and its offers commit in one SQLite
transaction. `--max-pages` may be increased when resuming. Omitting `--run-id` starts
a new timestamped observation series with fresh requests. Old observations remain
in SQLite; the All Offers sheet contains only the selected run, never stale rows
silently mixed into current results.

The database is `data/deals.sqlite`; evidence is under `data/evidence/RUN/RETAILER`.
Each response has its URL, HTTP status, collection timestamp, digest and raw body.
Store large evidence privately. Do not commit downloaded pages or price data to Git.

## Outputs

* `reports/deals.xlsx`: All Offers, Best Deals, Cross-Retailer Comparisons, Coverage
  Report, Errors and Limitations, Price History, and Validation Evidence.
* `reports/inventory.csv`: all unique offers for the run, independently of relevance.
* `reports/coverage.csv`, `validations.csv`, `errors.csv`: audit trails.
* `reports/executive_summary.md`: totals, retailer outcomes, up to 30 candidates,
  comparisons and exact collection limitations.
* `reports/run_manifest.json`: machine-readable totals and zero-AI confirmation.

The workbook exporter uses openpyxl so the requested Python-only pipeline runs in
GitHub without proprietary runtime libraries. It freezes headers, adds filters,
formats numeric prices, links product URLs and guards against formula injection.
Exports are read back and checked for expected sheets and row counts.

## Coverage definitions

| Status | Required meaning |
|---|---|
| VERIFIED COMPLETE | Known collection's reliable advertised product count matches unique product IDs, pagination terminates, no page failed/capped, and boundary pages recheck successfully. |
| PARTIAL | Failure, cap, repeat/loop, changing count, or advertised-count mismatch. |
| BLOCKED | Access or policy prevents useful collection. |
| UNVERIFIABLE | Missing reliable denominator or unsupported listing/pagination evidence, even when an endpoint was traversed to its end. |

Collection counts refer to **products**, not variants. Offers preserve each priced
variant. Duplicate memberships across collections are reported without counting
them as new offers. Coverage percentage is blank when the denominator is unknown,
zero or inconsistent. First, middle and final product pages are rechecked where
possible, and reproducibly random detail-page samples check titles and prices.
No entire-store completeness claim is made. Homepage/nav/link discovery can miss
unlinked campaigns, email coupons and account/cart-only offers.

## Price semantics and matching

Missing data stays missing. `reference_price` holds Shopify compare-at or an HTML
struck-through amount, labelled with its provenance. It is **not** put into MSRP or
verified past selling price. `normal_advertised_price` and `discount_percentage`
stay blank without explicit evidence. `reference_discount_percentage` is a separate
arithmetic comparison and does not prove a real saving. Large explicit MSRP gaps
are flagged for history review, never called fake. The current adapters rarely
expose a separately labelled MSRP.

`verified_effective_price` means directly displayed listing price with no
unverified coupon adjustment, before shipping/tax. Coupon eligibility is not guessed.
Coupon-specific extraction and application are not yet implemented; fields remain
blank and cart-only savings are outside verified coverage.

Matching uses checksum-valid GTIN, or conservative manufacturer-model/title guards,
with condition, currency, variant and bundle safeguards. Retailer SKUs are local,
never global match IDs. Exact normalized-title matches are only review candidates.
Unknown conditions/brands suppress comparison. Finishes/generations/numbers are
not stripped out. No offers are merged away. Shopify vendor fields can contain a
distributor; this is flagged. When identifiers are scarce, zero cross-retailer
comparisons is preferable to false equivalence.

Relevance rules prioritize fretless/five-string bass, unusual effects, expressive
control, recording and practice. Conventional effects and clearly overlapping
owned gear are demoted. The list is a set of candidates, not 30 forced purchases;
price, condition, specifications and actual usefulness still need review.

## GitHub: download a report without an AI agent

1. Open **Actions → Collect music promotions → Run workflow** on `main`.
2. Leave retailer/run ID blank for fresh collection from all configured stores.
3. Open the completed run to read its Summary, then download the
   `music-deals-RUN_ID-ATTEMPT` artifact.
4. Unzip and open `reports/deals.xlsx` or `reports/executive_summary.md`.

The workflow also runs tests. Source changes pushed to `main` trigger a validation
collection; branches and PRs run tests only. Runs are serialized and have a
120-minute ceiling. Report/data/log artifacts upload even if collection fails.
Report checkpoints are written after each retailer; SQLite retains each completed
page even if a process is stopped partway through a retailer.

The next run restores the most recent unexpired main-branch snapshot's database and
evidence using the workflow's temporary read-only GitHub token. **Artifacts expire
after 30 days** (or sooner if repository policy requires). They are not a permanent
backup. If there is no prior snapshot, the workflow announces a fresh start. For
long-term price history, periodically download the artifacts or add your own durable
storage. Do not upload access tokens or customer data. Public-repository artifact
visibility follows GitHub's repository access rules.

GitHub-hosted runner usage/storage is governed by GitHub's account limits. ChatGPT
Plus does not pay for GitHub usage; this project makes no paid AI calls.

## Enable recurrence only after validation

Scheduling is commented out in `.github/workflows/collect.yml`. First inspect the
live artifact, counts, errors and sample checks. Then uncomment the `schedule`
block on the default branch. `0 11 * * *` runs at approximately 6am New York in
standard time / 7am in daylight time. GitHub schedules can be delayed, and inactive
public repositories can have schedules disabled. Manual workflow dispatch remains
available. Leave scheduling disabled if policy/coverage issues are unresolved.

## Maintaining and extending collectors

1. Inspect robots and terms; do not flip a policy block just because HTTP works.
2. For accessible stores, review `terms_text()` output and update the reviewed
   `terms_sha256` only after checking policy changes. This fingerprint deliberately
   stops unattended collection if reviewed terms change.
3. Add an adapter and sanitized representative fixtures. Validate counts, variants,
   pagination endpoints, empty tails, loops, stock and price labels.
4. Run tests, then a capped live check, then full known collections.
5. Compare exported rows with original listings and read the coverage report.

Development inspection scripts in `scripts/` are not production crawlers. The
production entry point enforces the reviewed policy configuration. Disabled stores
need permission or an authorized feed, not anti-bot workarounds.

## Architecture

`http.py` handles policy, evidence and retries; `collectors.py` extracts listing
formats; `pipeline.py` discovers/traverses/verifies; `store.py` persists atomic
checkpoints and history; `model.py` defines price/identity semantics; `report.py`
exports and ranks deterministically. Configuration is JSON, not executable code.
There is no model dependency and optional AI enrichment is deliberately rejected
if enabled because it has not been implemented.
