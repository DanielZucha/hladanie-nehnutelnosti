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
- **sreality.cz**: Daily new-arrivals scrape via JSON API (`estate_age=2`, ~50/day). Background dataset from one-time full scrape.
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

## Report Categories (email reports -- new arrivals only)
- Red dot: Top 10 by composite score
- Green dot: Top 5 best price/m2
- Blue dot: Top 5 best location + greenery

## Active Goals
- [x] Region filter + sreality API scrape (merged to devel, CI passing)
- [ ] Register ceskereality.cz alerts on throwaway account
- [ ] Tune location_scores in config.yaml based on viewing feedback
- [ ] Monitor parser stability as portal email formats change
- [ ] Verify first automated cron run fires (daily 7:00 UTC)

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

### 2026-03-31: Region filter, sreality scrape, market analysis, architecture refactor
- Discovered sreality `watchdog` param doesn't filter API results -- was pulling 68k nationwide
- Added region filter (text + GPS bbox) for Praha + Praha-východ/Kolín/Nymburk
- One-time full scrape seeded 2,872 listings as background dataset (on Drive)
- Peer-group market analysis (apt/house x prague/region) with z-scores; PDF on Drive
- Refactored daily flow: sreality new arrivals via `estate_age=2` (~50/day) + iDNES/ceske emails
- OSM enrichment runs on new arrivals only (feasible at ~50/day, ~2 min)
- Email reports now filter to new arrivals since last report; fixed 10 red + 5 green + 5 blue picks
- Price-on-request scored at 50; client-side price cap enforcement (API filter unreliable)
- CI passing at 1m17s. Pandas dtype fix for str/int assignment in deduplicate_and_merge
- Next: verify first cron run, tune scoring after viewings
