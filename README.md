# Weekly Literature Monitor

Automated weekly monitoring of academic publications in environmental economics, industrial ecology, and MRIO research. Creates GitHub Issues with relevant new papers.

## Features

- **Automated Weekly Runs**: GitHub Actions runs every Monday at 8:00 UTC
- **29 Curated Journals**: Nature family, Science family, ES&T, J. Industrial Ecology, etc.
- **Multi-tier Relevance Scoring**: Papers matching multiple research themes get higher priority
- **Incremental Updates**: Only reports new papers since last run
- **GitHub Issue Notifications**: Formatted reports with paper details and DOI links

## Research Topics Monitored

| Tier | Topic | Examples |
|------|-------|----------|
| **T1** | IO/MRIO Methods | MRIO, EEIO, EXIOBASE, WIOD, supply chain |
| **T2** | Carbon Emissions | Carbon footprint, GHG, embodied carbon, Scope 1/2/3 |
| **T3** | Environmental Footprints | Water footprint, material flow, ecological footprint |
| **T4** | Infrastructure & Capital | GFCF, capital formation, construction emissions |
| **T5** | Trade & Globalization | Carbon leakage, trade-embodied, environmental inequality |
| **T6** | Climate Targets | Net-zero, Paris Agreement, carbon neutrality |
| **T7** | AI & Datacenters | AI carbon, data center energy, digital infrastructure |

Papers matching multiple tiers receive bonus scores and higher priority.

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
│   └── weekly-monitor.yml      # GitHub Actions workflow
├── config/
│   ├── journals.json           # Monitored journals (29 Q1/Q2)
│   └── keywords.json           # Keyword tiers and scoring rules
├── src/
│   ├── weekly_monitor.py       # Main entry point
│   ├── crossref_client.py      # Crossref API client
│   ├── relevance_filter.py     # Multi-tier keyword scoring
│   ├── state_manager.py        # State persistence
│   ├── github_issue.py         # Issue notification
│   └── paper_utils.py          # Utilities
├── state/
│   └── monitor_state.json      # Run state (auto-updated)
└── README.md
```

## Configuration

### Adding/Removing Journals

Edit `config/journals.json`:
- Add ISSN to `issn_list` array
- Add journal details to appropriate category

### Customizing Keywords

Edit `config/keywords.json`:
- Add keywords to existing tiers
- Adjust scoring in `scoring_rules`
- Add new tiers following the existing pattern

## CLI Options

```
python weekly_monitor.py [OPTIONS]

Options:
  --days N      Number of days to look back (default: 7)
  --dry-run     Run without creating Issue or updating state
  --quiet       Suppress verbose output
```

## Output Example

GitHub Issue created:

```markdown
# Weekly Literature Monitor Report

**Period:** 2026-01-16 to 2026-01-23
**Generated:** 2026-01-23 08:00 UTC

## Summary
- Total papers fetched: 127
- Relevant papers found: 23
- Journals monitored: 29

| Priority | Count |
|----------|-------|
| HIGH | 5 |
| MEDIUM | 12 |
| LOW | 6 |

---

## HIGH Priority Papers

### 1. Global carbon footprint of infrastructure investments...

**Authors:** Zhang, Li, Wang et al.
**Journal:** Nature Sustainability (2026)
**DOI:** [10.1038/s41893-026-00123-4](https://doi.org/10.1038/s41893-026-00123-4)
**Relevance Score:** 25 (HIGH)
**Matched Tiers:** T1 Core Io, T2 Carbon Emissions, T4 Infrastructure Capital
**Keywords:** MRIO, carbon footprint, infrastructure, capital formation
```

## Requirements

- Python 3.8+
- No external dependencies (uses only standard library)

## License

MIT License

## Related Projects

- [literature-review-system](https://github.com/yourusername/literature-review-system) - Full literature management system
- [Paper Picnic](https://paper-picnic.com/) - Weekly political science paper digest (inspiration)
