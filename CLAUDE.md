# Home Search -- Prague Property Monitor

## Overview
Autonomous property monitoring pipeline for Prague + Central Bohemia east
(okres Praha-východ, Kolín, Nymburk). Two data flows: email alerts for new-deal
notifications, and daily full-region scrape from sreality API for market context.
Scores listings, syncs CSV to Google Drive, and emails Czech-language reports.

## Tech Stack
- **Language**: Python 3.12
- **Email**: IMAP/SMTP with Gmail App Passwords (no GCP)
- **Drive sync**: rclone with built-in OAuth (remote: `hladanie-nehnutelnosti`)
- **Scheduling**: GitHub Actions (daily parse + Tue/Fri reports)
- **Repo**: DanielZucha/hladanie-nehnutelnosti, branch: `devel`

## Data Sources
- **sreality.cz**: Email alerts + daily full-region scrape via JSON API `/api/cs/v2/estates`
- **reality.idnes.cz**: Native "hlidaci pes" alerts, listings parsed from email HTML
- **ceskereality.cz**: Parser written, awaiting alert registration

## Scoring Model (weights sum to 1.0)
| Dimension | Weight | Notes |
|-----------|--------|-------|
| Price attractiveness | 35% | Inverse price/sqm + penalty per 1M above 12M CZK cap |
| Greenery | 25% | Keywords + OSM park proximity (300m radius) |
| Transit | 20% | Prague=100, non-Prague: train <3km=100, <6km=50, else=0 |
| Room count | 10% | 4+=100, 3+1=70, 3+kk=60 |
| Property type | 10% | House=100, apt w/ garden=70, apt w/o=40 |

House premium tolerance: 25% (effective price reduced before scoring).

## Report Categories (email reports)
- Red dot: Top 10 by composite score (P95 percentile threshold)
- Green dot: Top 5 best price/m2
- Blue dot: Top 5 best location + greenery

## Active Goals
- [ ] Merge feature/region-filter -> devel, monitor first automated daily run
- [ ] Register ceskereality.cz alerts on throwaway account
- [ ] Tune location_scores in config.yaml based on viewing feedback
- [ ] Monitor parser stability as portal email formats change
- [ ] Improve OSM enrichment throughput (batch queries or local Overpass instance)

## Key Files
| File | Purpose |
|------|---------|
| `config.yaml` | All tuneable parameters (weights, thresholds, senders) |
| `src/pipeline.py` | Entry points: daily, report-all, parse, scrape, score |
| `src/parsers/sreality.py` | Sreality JSON API: email parsing + region scrape |
| `src/parsers/idnes.py` | iDNES email HTML parser |
| `src/region_filter.py` | Text + GPS bbox filter for target regions |
| `src/scorer.py` | Weighted scoring + categorized picks |
| `src/reporter.py` | Czech HTML report + scatter plot |
| `src/market_analysis.py` | One-time peer-group z-score analysis (not in daily flow) |

## Project Journal

### 2026-03-30: Initial build and deployment
- Built full pipeline from scratch: parsers, enricher, scorer, reporter, Drive sync
- Pivoted from Gmail/Drive API to IMAP/SMTP + rclone (no GCP needed)
- Tested with real emails (sreality + iDNES), 83 unique listings scored
- Deployed to GitHub Actions, all workflows passing
- Next: Register ceskereality alerts, tune scoring after first week of data

### 2026-03-31: Region filter, full-region scrape, market analysis
- Discovered sreality `watchdog` param doesn't filter API results -- was pulling 68k nationwide listings
- Added region filter (text + GPS bbox) for Praha + Praha-východ/Kolín/Nymburk
- Added `scrape_region_listings()`: 2 targeted API queries (apts 3+kk+, houses, <=17M), ~4,200 listings in 36s
- Sreality API price filter unreliable -- 1,292 listings above cap slipped through; added client-side enforcement
- Price-on-request (price=1, n=296) now scored at 50 instead of None (was inflating composite via weight redistribution)
- Built peer-group market analysis (apt/house x prague/region) with z-scores; PDF with clickable links uploaded to Drive
- Fixed email report: switched from absolute threshold (85) to P95 percentile + cap of 10
- OSM Overpass can't handle bulk enrichment (~1 req/min under load); capped at 100 new records per run
- Seeded master CSV with 2,872 scored listings, uploaded to Drive
- Next: merge feature/region-filter to devel, monitor first automated daily run, tune scoring after viewings
