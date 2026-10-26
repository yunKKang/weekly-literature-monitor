# Weekly Literature Monitor

Weekly GitHub Issues and a local search workbench for physical capital footprints and prospective MRIO / input-output methods linked to IAM and SSP scenarios.

## Research scope

Papers qualify through one of five channels. Trade and policy terms require environmental or IO context; generic investment or economics alone is insufficient.

| Channel | Required evidence |
| --- | --- |
| Capital footprints | Physical investment/capital stocks + environmental impacts |
| Capital IO accounting | Physical investment/capital stocks + MRIO/EEIO |
| Capital goods trade | Physical capital + trade/supply chains + environmental/IO context |
| Capital scenarios | Physical capital + scenarios/policy + environmental/IO context |
| Prospective IO methods | MRIO/input-output + IAM/SSP/future scenarios; GFCF is optional, including household studies |

Ambiguous standalone `GCF`, `FCF`, `CFC`, `PIM`, `retirement`, `vintage`, and software/algorithm terms were removed. Full terms such as *consumption of fixed capital* and *perpetual inventory method* remain. Generic digital engineering bonuses were removed. Finance exclusions apply to every capital channel. Synonyms within a concept count once per field: `GFCF` and `gross fixed capital formation` receive the same score.

`config/keywords.json` supplies rule scoring, exclusions and concept groups; `topics/gfcf_environment.yaml` supplies the topic gate and AI prompt. Both configurations have regression coverage. HIGH is 20 points with a specificity check; MEDIUM is 12. Investment concepts receive 20/12 points in title/abstract, domain matches 5/3, capped at one domain match per field. Scoring controls candidate review order; publication also requires an AI verdict.

## Journal selection

The weekly default is **31 core journals**. **9 supplementary journals** run on the first Monday of each month, with an independent date watermark and a 40-day initial lookback. Explicit `--include-supplementary` runs them immediately. The catalog contains 40 unique journals with validated ISSN checksums and consistent pool mappings.

Core journals retain industrial ecology, LCA, IO methods, environmental systems and major climate/energy journals. Economic Systems Research and Ecological Economics remain because they directly support MRIO methods. Supplementary journals include Review of Income and Wealth, Research Policy, Energy Economics and selected buildings, transport, food and water journals. Geoscientific Model Development and Climatic Change were added. Broad macroeconomics, finance and generic computer engineering pools were removed. Journal selection reflects topic fit rather than a numerical quality ranking.

Scheduled conference searches are disabled: Crossref conference-title search is fuzzy and can match unrelated proceedings. The conference client remains supported and tested; cursor requests omit both `sort` and `order`.

## AI review and evidence

Scheduled, synchronous and asynchronous topic searches use `core/review_service.py` and one pipeline instance/budget per batch. Each MEDIUM/HIGH candidate receives one abstract-based review, using `deepseek-flash` with JSON output and thinking disabled. The retired `deepseek-chat` alias is no longer used. Default limits: 200 attempted papers and USD 2 estimated cost per run; requests are serial. Cost accounting uses conservative configured rates, not an invoice.

Each review records status, provider, model, timestamp, verdict, confidence and reason. `completed` with a positive high/medium confidence verdict is eligible for a report. `failed`, `missing_credentials`, `missing_abstract`, `uncertain` and `budget_skipped` remain pending. Low confidence or absent evidence cannot approve publication. Missing Crossref abstracts are supplemented by DOI-linked OpenAlex metadata when available. Manual results retain rule scores and clearly expose incomplete AI status in the stored audit.

Successful verdicts are cached by DOI, title, abstract, journal, year, model, prompt and configuration version. Changed evidence or rules forces another review. Historical benchmark DOIs are evaluation fixtures, never injected into live search results.

## Recovery and publication safety

1. Fetch the complete date range. A failed Crossref batch, repeated cursor, invalid response or page limit aborts before publication or checkpoint advancement. GET requests back off on 429/5xx; Issue POSTs are never blindly retried.
2. Normalize DOIs and reconcile all report Issues, including closed reports and full DOI manifests. `seen_dois` means observed metadata, not reviewed or published. Reprocessing never republishes a DOI already in a report.
3. Persist candidate metadata and AI evidence atomically to `state/review_cache.json` before publication. Pending candidates are retried even if the acquisition window has moved.
4. Save the acquisition checkpoint only after retrieval and notification succeed. `last_review_complete_date` advances only with no pending candidates; `review_pending_count` exposes the remaining queue. The acquisition date may advance while the durable review queue is pending.
5. Actions commits only the checkpoint and review queue, including pending evidence after publication failures. All publication workflows share one concurrency group. An Issue created before a failed state commit is discovered on the next run and skipped.

A catch-up report covers the missed interval; it cannot reconstruct what Crossref exposed on each historical Monday. Do not manually move the checkpoint or clear the DOI history to recover.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/litmon init-db
.venv/bin/litmon serve
# http://127.0.0.1:8000
```

For scheduled AI review, add a repository Actions secret named **DEEPSEEK_API_KEY** under Settings → Secrets and variables → Actions. `GITHUB_TOKEN` is provided by Actions. `OPENALEX_API_KEY` is optional for abstract enrichment. Keep actual credentials out of Git; `.env.example` contains names only. Any credential previously committed to Git should be rotated, since deleting the file does not remove repository history.

```bash
# Real synthetic AI connectivity check: no publication or checkpoint changes
python src/weekly_monitor.py --check-ai

# Rules-only preview: no AI charges, Issue creation, cache or checkpoint writes
python src/weekly_monitor.py --dry-run --quiet

# Reapply current rules over an old interval; published DOI history still applies
python src/weekly_monitor.py --reprocess-since 2026-07-25 --include-supplementary --quiet
```

The workflow runs every Monday at 08:00 UTC. For a manual catch-up, choose **Weekly Literature Monitor → Run workflow**, set `reprocess_since` if needed, and leave `dry_run` unchecked after reviewing a preview. Missing/invalid AI credentials fail the connectivity step explicitly before publication. CLI dates must be ISO `YYYY-MM-DD` and cannot be in the future.

## Validation

```bash
python -m compileall -q src literature_monitor
python -m pytest tests/ -q
litmon topic validate gfcf_environment
```

CI runs Python 3.10, 3.11 and 3.12. Tests cover cursor compatibility, partial fetch failures, DOI normalization, Issue/state failure recovery, dry-run immutability, topic boundaries, monthly scope, concept scoring, AI contract validation, budget/cache behavior and shared manual/async review.

MIT License.
