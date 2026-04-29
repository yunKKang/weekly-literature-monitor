# Weekly Literature Monitor

Automated weekly monitoring of academic publications for **GFCF (Gross Fixed Capital Formation) environmental impact research**. Creates GitHub Issues with relevant new papers.

## Core Pain Point

Researchers tracking capital formation, embodied impacts, and MRIO/EEIO methods face a high-noise discovery problem: relevant papers often avoid the exact term "GFCF", while generic investment searches return finance, ESG, and portfolio literature that is not useful for environmental footprint work. This project turns that recurring manual scan into a reproducible weekly filter with transparent vocabulary, scoring, and state tracking.

## Core Logic Flow

The monitor is a deterministic retrieval and ranking pipeline, not a multi-agent system and not an LLM long-chain reasoning workflow. Its logic is:

1. Load journal, conference, keyword, and prior-run state configuration.
2. Query Crossref for newly published works across curated journal/conference pools.
3. Deduplicate by DOI and remove papers already seen in previous runs.
4. Apply hard-threshold filters: investment/capital vocabulary must co-occur with at least one domain pipeline.
5. Score surviving papers with weighted title/abstract matches, concept deduplication, capped bonuses, negative keyword exclusions, and HIGH-priority consistency checks.
6. Publish a GitHub Issue report, or optionally export an Obsidian Markdown note.
7. Update state only after successful notification/export so failed runs can be retried safely.

## Architecture: GFCF-Driven Pipeline System (v2.1)

This system uses a **GFCF-first** approach where investment/capital formation is the primary research object, and MRIO/EEIO serves as a methodological tool.

### Pipeline System with Hard Thresholds

Papers must pass a **hard threshold** (Boolean AND condition) to be considered:

| Pipeline | Hard Threshold | Description |
|----------|----------------|-------------|
| **P1: Environmental** | Investment terms AND Environmental impact terms | Core GFCF footprint research (carbon, water, material, land, biodiversity) |
| **P2: MRIO/EEIO** | Investment terms AND IO method terms | Capital endogenization, investment-embodied emissions |
| **P3: Trade** | Investment terms AND Trade/globalization terms | Investment goods trade, cross-border transfers |
| **P4: Policy** | Investment terms AND Scenario/policy terms | Decarbonization pathways, lock-in effects, stranded assets |

### Journal Pools (v2.1)

| Pool | Focus | Journals | Priority |
|------|-------|----------|----------|
| **Main Pool** | GFCF x Environmental Impact | JIE, IJLCA, ES&T, Nature Sustainability, Applied Energy, etc. | 1 |
| **Pool A1** | SNA/National Accounts & Capital Measurement | Review of Income and Wealth, J. Productivity Analysis, Research Policy | 2 |
| **Pool A2** | Macro Growth & Productivity (lower frequency) | J. International Economics, J. Development Economics | 3 |
| **Pool B** | Built Environment, Infrastructure & Transport | Building and Environment, Cities, Transportation Research Part D, Water Research | 2 |
| **Pool C** | Digital Infrastructure & Datacenters | Sustainable Computing, Future Generation Computer Systems, IEEE Micro | 2 |
| **General** | High-Impact Journals | Nature, Science, PNAS, Nature Communications | 3 |

## Features (v2.1 Enhancements)

- **Automated Weekly Runs**: GitHub Actions runs every Monday at 8:00 UTC
- **70+ Curated Journals**: Organized in 6 pools by research relevance
- **Hard Threshold Filtering**: Only papers matching GFCF vocabulary + domain terms pass
- **Concept Deduplication**: Same concept counted only once regardless of synonyms (prevents keyword stuffing)
- **Bonus Capping**: Method/policy bonuses capped at 6 points maximum
- **Negative Keywords**: Filters out financial/ESG noise (portfolio, stock market, etc.)
- **Consistency Check**: HIGH priority requires asset type OR methodology match
- **Multi-footprint Support**: Carbon, water, material, land, biodiversity, nitrogen, energy footprints
- **Weighted Field Scoring**: Title 8x, Abstract 5x

## GFCF Vocabulary Coverage (v2.1)

The system captures papers that may not explicitly use "GFCF" but discuss related concepts.

### Investment Terms (63 patterns)

| Category | Keywords |
|----------|----------|
| **Core GFCF** | `GFCF`, `GCF`, `FCF`, `FBCF`, `gross fixed capital formation`, `capital formation` |
| **Investment** | `investment`, `fixed investment`, `capital expenditure`, `CAPEX`, `capital goods` |
| **Capital Stock** | `capital stock`, `net stock`, `capital accumulation`, `capital deepening`, `capital intensity` |
| **Depreciation** | `depreciation`, `CFC`, `consumption of fixed capital`, `scrappage`, `retirement`, `decommissioning` |
| **Lifetime/PIM** | `perpetual inventory method`, `PIM`, `asset lifetime`, `service life`, `vintage` |
| **Capital Services** | `capital services`, `KLEMS`, `PWT`, `Penn World Table` |
| **Capitalization** | `capitalization`, `capitalized`, `data capitalization` |

### Asset Types (SNA Categories)

| Category | Keywords |
|----------|----------|
| **Structures** | buildings, construction, infrastructure, civil engineering, built environment, housing |
| **Machinery & Equipment** | machinery, industrial equipment, plant and equipment, capital equipment |
| **Transport Equipment** | vehicles, fleet, aircraft, ships, rolling stock, trucks |
| **ICT Equipment** | servers, semiconductors, computers, hardware, GPU, TPU, processors |
| **Intangibles/IPP** | software, databases, data assets, R&D capital, intellectual property, organizational capital, digital assets |

### Environmental Impact Terms (Multi-footprint)

| Footprint Type | Keywords |
|----------------|----------|
| **Carbon** | carbon, GHG, CO2, emissions, embodied, carbon accounting |
| **Water** | water footprint, virtual water, embodied water |
| **Material** | material footprint, embodied materials |
| **Land** | land footprint, land use change, ecological footprint |
| **Biodiversity** | biodiversity footprint, biodiversity loss |
| **Nitrogen** | nitrogen footprint |
| **Energy** | energy footprint, embodied energy |
| **Upfront** | upfront emissions, upfront carbon |

### Supplementary Themes

| Theme | Keywords | Bonus |
|-------|----------|-------|
| **Digital Infrastructure** | datacenters, cloud computing, AI infrastructure, hyperscale, PUE, WUE, liquid cooling, GPU energy, LLM | +5 |
| **Material Resources** | material footprint, circular economy, steel, cement, aluminum, copper, rare earth | +3 |
| **Water & Land** | water footprint, virtual water, land use, ecological footprint | +3 |

## Quick Start

### Manual Run (Local)

```bash
cd src
python weekly_monitor.py --dry-run --days 7
```

### GitHub Actions

1. Fork this repository
2. Enable GitHub Actions in your fork
3. The workflow runs automatically every Monday
4. Or trigger manually from Actions tab

### Required Secrets

For GitHub Issue creation, the `GITHUB_TOKEN` is automatically provided by GitHub Actions. No additional configuration needed.

## Project Structure

```
weekly-literature-monitor/
├── .github/workflows/
│   ├── ci.yml                  # Compile and test quality gate
│   └── weekly-monitor.yml      # GitHub Actions workflow
├── config/
│   ├── journals.json           # Journal pools (6 pools, 70+ journals)
│   └── keywords.json           # GFCF vocabulary & pipeline definitions
├── src/
│   ├── weekly_monitor.py       # Main entry point
│   ├── crossref_client.py      # Crossref API client
│   ├── relevance_filter.py     # Pipeline-based filtering with v2.1 features
│   ├── state_manager.py        # State persistence
│   ├── github_issue.py         # Issue notification
│   ├── obsidian_report.py      # Optional Obsidian Markdown export
│   └── paper_utils.py          # Utilities
├── state/
│   └── monitor_state.json      # Run state (auto-updated)
├── tests/
│   └── test_monitor_reliability.py
├── LICENSE
├── pyproject.toml
└── README.md
```

## Configuration

### Modifying Journal Pools

Edit `config/journals.json`:
- Add journals to existing pools or create new categories
- Each pool has a priority level for weighting
- ISSN list is automatically derived from pool definitions
- v2.1 splits Pool A into A1 (SNA core) and A2 (macro growth)

### Customizing GFCF Vocabulary

Edit `config/keywords.json`:
- Add investment terms to `gfcf_vocabulary.investment_terms`
- Define concept groups for deduplication in `concept_groups`
- Define asset types in `gfcf_vocabulary.asset_types`
- Modify pipeline domain terms in `pipelines.*`
- Add negative keywords in `negative_keywords`
- Adjust scoring weights in `scoring_rules`

### Pipeline Configuration (v2.1)

Each pipeline in `keywords.json` has:
- `hard_threshold`: Defines required term categories (AND logic)
- `domain_terms`: Pipeline-specific vocabulary
- `bonus_terms`: Extra scoring for specialized terminology

### Negative Keywords (v2.1)

Exclusion patterns filter noise per pipeline:
- **P1/P2 (Accounting)**: Block financial terms (portfolio, stock market, hedge fund, etc.)
- **P4 (Policy)**: Allow climate finance, block pure finance (asset pricing, equity return)

### Consistency Check (v2.1)

For HIGH priority (score ≥20), papers must also match:
- At least one SNA asset type, OR
- Capital measurement terminology (CFC, PIM, lifetime, vintage, depreciation), OR
- Embodied impact terminology (embodied, upfront, capital goods emissions)

## CLI Options

```
python weekly_monitor.py [OPTIONS]

Options:
  --days N      Number of days to look back (default: 7)
  --dry-run     Run without creating Issue or updating state
  --quiet       Suppress verbose output
  --overlap-days N
                Days to overlap before the last run date to catch delayed Crossref records
```

## Scoring System (v2.1)

### Hard Threshold (Boolean)
- Must match at least one **investment term** AND one **domain term**
- Papers failing this threshold are excluded entirely
- Negative keyword match → blocked (per-pipeline basis)

### Concept Deduplication
- Same concept counted only once (e.g., "GFCF", "gross fixed capital formation", "capital formation" = 1 match)
- Prevents keyword-stuffed papers from gaming the system

### Ranking Score (Weighted)

| Component | Title Weight | Abstract Weight | Cap |
|-----------|--------------|-----------------|-----|
| Investment terms | 8 | 5 | - |
| Domain terms | 8 | 5 | - |
| Bonus terms (method) | +3 each | - | 6 max |
| Bonus terms (policy) | +2 each | - | 6 max |
| Asset type match | +2 each | - | - |
| Theme match | +3-5 each | - | - |
| Multi-pipeline | +5 per extra | - | - |

### Priority Levels

| Priority | Score Threshold | Consistency Required |
|----------|-----------------|----------------------|
| HIGH | ≥ 20 | Yes (asset type OR methodology) |
| MEDIUM | ≥ 12 | No |
| LOW | > 0 (passed threshold) | No |

## Output Example

GitHub Issue created:

```markdown
# Weekly Literature Monitor Report

**Period:** 2026-01-19 to 2026-01-26
**Generated:** 2026-01-26 08:00 UTC

## Summary
- Total papers fetched: 215
- Relevant papers found: 31
- Journals monitored: 70+

| Priority | Count |
|----------|-------|
| HIGH | 8 |
| MEDIUM | 15 |
| LOW | 8 |

---

## HIGH Priority Papers

### 1. Carbon footprint of infrastructure investment in emerging economies...

**Authors:** Zhang, Li, Wang et al.
**Journal:** Nature Sustainability (2026)
**DOI:** [10.1038/s41893-026-00123-4](https://doi.org/10.1038/s41893-026-00123-4)
**Relevance Score:** 28 (HIGH)
**Pipeline:** GFCF x Environmental Impact Accounting
**Matched Terms:** investment, infrastructure, carbon footprint, embodied emissions
**Asset Types:** structures, machinery_equipment
**Consistency:** ✓ Passed (asset type match)
```

## Requirements

- Python 3.10+
- No external dependencies (uses only standard library)

## Quality Gate

Run these checks before opening a pull request:

```bash
python -m compileall src
python -m unittest discover -s tests
```

The `CI` workflow runs the same checks on push and pull requests.

## Optional Obsidian Export

`src/obsidian_report.py` exports the filtered results to Markdown. By default it writes to `obsidian-exports/`, which is ignored by Git. Set `OBSIDIAN_OUTPUT_DIR` or pass `--output-dir` to write into a local vault path.

## Design Philosophy

This system is designed around five key principles:

1. **GFCF-First**: Investment/capital formation is the primary research object, not MRIO methodology
2. **Hard Thresholds**: Eliminates noise by requiring both investment vocabulary AND domain relevance
3. **Asset-Aware**: Captures the full SNA asset spectrum including intangibles/IPP often missed by keyword searches
4. **Multi-footprint**: Extends beyond carbon to water, material, land, biodiversity, and energy footprints
5. **Consistency Verification**: HIGH priority papers must demonstrate concrete asset or methodology specificity

## Version History

| Version | Date | Changes |
|---------|------|---------|
| **2.1** | 2026-01-26 | Pool A split (A1/A2), concept dedup, bonus capping, negative keywords, consistency check, multi-footprint, datacenter engineering terms |
| **2.0** | 2026-01-20 | GFCF-first architecture, 4 pipelines, hard thresholds |
| **1.0** | 2026-01-15 | Initial release, MRIO-driven tier system |

## License

MIT License
