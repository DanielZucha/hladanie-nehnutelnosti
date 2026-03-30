# Home Search -- Prague Property Monitor

## Overview
Autonomous property monitoring pipeline for Prague + Central Bohemia east.
Parses email alerts from Czech portals, enriches via APIs (sreality JSON, OSM Overpass),
scores listings, syncs CSV to Google Drive, and emails Czech-language reports.

## Tech Stack
- **Language**: Python 3.12
- **Email**: IMAP/SMTP with Gmail App Passwords (no GCP)
- **Drive sync**: rclone with built-in OAuth (remote: `hladanie-nehnutelnosti`)
- **Scheduling**: GitHub Actions (daily parse + Tue/Fri reports)
- **Repo**: DanielZucha/hladanie-nehnutelnosti, branch: `devel`

## Data Sources
- **sreality.cz**: Email alerts -> saved search IDs -> JSON API `/api/cs/v2/estates`
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

## Report Categories
- Red dot: ALL listings with composite score > 85 (no cap)
- Green dot: Top 5 best price/m2
- Blue dot: Top 5 best location + greenery

## Active Goals
- [ ] Register ceskereality.cz alerts on throwaway account
- [ ] Tune location_scores in config.yaml based on viewing feedback
- [ ] Monitor parser stability as portal email formats change

## Key Files
| File | Purpose |
|------|---------|
| `config.yaml` | All tuneable parameters (weights, thresholds, senders) |
| `src/pipeline.py` | Entry points: daily, report-all, parse, score |
| `src/parsers/sreality.py` | Sreality JSON API integration |
| `src/parsers/idnes.py` | iDNES email HTML parser |
| `src/scorer.py` | Weighted scoring + categorized picks |
| `src/reporter.py` | Czech HTML report + scatter plot |

## Project Journal

### 2026-03-30: Initial build and deployment
- Built full pipeline from scratch: parsers, enricher, scorer, reporter, Drive sync
- Pivoted from Gmail/Drive API to IMAP/SMTP + rclone (no GCP needed)
- Tested with real emails (sreality + iDNES), 83 unique listings scored
- Deployed to GitHub Actions, all workflows passing
- Next: Register ceskereality alerts, tune scoring after first week of data
