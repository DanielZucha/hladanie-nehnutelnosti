# Home Search -- Prague Property Monitor

## Overview
Autonomous property monitoring pipeline for Prague + the Praha-východ belt up to
Úvaly/Nehvizdy (on the way to Pardubice). Two sources: sreality v1 search API
(full filtered inventory daily) and iDNES "hlídací pes" emails to the throwaway
inbox (secret `EMAIL_ADDRESS`). Hard rules in `config.yaml` `search:`: 79+ m2,
apartment or house, 3+kk+, 6-14M CZK, not panel, train <= 4 km outside Prague,
Škvorec excluded by name (`exclude_towns`: bus-only to a train in practice).
Scores listings, syncs CSV to Google Drive, and emails Czech-language reports.

## Tech Stack
- **Language**: Python 3.12
- **Email**: IMAP/SMTP with Gmail App Passwords (no GCP)
- **Drive sync**: rclone with built-in OAuth (remote: `hladanie-nehnutelnosti`)
- **Scheduling**: GitHub Actions, 7:00 UTC. `daily_parse.yml` Sun/Mon/Wed/Thu/Sat; `biweekly_report.yml` Tue/Fri (ingests too). A keepalive job re-enables both via `gh api .../enable` (the gautamkrishnar action is blocked by GitHub)
- **Repo**: DanielZucha/hladanie-nehnutelnosti, default branch: `main` (renamed from `devel` 2026-11-25). **Public repo**: no email addresses in commits, logs or PR text

## Data Sources
- **sreality.cz**: `/api/v1/estates/search` with server-side filters (v2 is 404 since 2026). robots.txt disallows bots: keep volume low.
- **reality.idnes.cz**: "hlídací pes" alerts; edit via the "přehled všech psů" link in an alert email (no login). Detail page -> wall type/area; Nominatim -> GPS; `src/stations.json` -> train distance
- **ceskereality.cz**: Parser written, awaiting alert registration

## Scoring Model (weights in config.yaml, sum to 1.0)
| Dimension | Weight | Notes |
|-----------|--------|-------|
| Price attractiveness | 45% | Inverse price/sqm + penalty per 1M above 12M CZK |
| Transit | 25% | Prague=100, else station <3km=100, <=4km=50 |
| Room count | 15% | 4+=100, 3+1=70, 3+kk=60 |
| Property type | 15% | House=100, apt w/ garden=70, apt w/o=40 |

House premium tolerance: 25% (effective price reduced before scoring).

## Report Categories (Tue + Fri)
Listings first seen after the last sent report. The send date lives in Drive `last_report.txt`
(fallback: fixed Tue/Fri window), so a manual test report does not repeat in the next one.
- Red dot: Top 10 by composite score
- Green dot: Top 5 best price/m2
- Blue dot: Top 5 largest usable area

## Active Goals
- [x] Restart with narrowed rules (2026-11-25); test report sent to all 4 recipients
- [x] iDNES watchers (4) updated to the new rules (2026-11-25)
- [ ] Verify Fri 27 Nov report shows only listings first seen 26-27 Nov
- [ ] Check run logs for a real iDNES alert parsing to > 0 listings
- [ ] Register ceskereality.cz alerts on throwaway account

## Key Files
| File | Purpose |
|------|---------|
| `config.yaml` | All tuneable parameters (weights, thresholds, senders) |
| `src/pipeline.py` | Entry points: daily, report-all, parse, scrape, score |
| `src/parsers/sreality.py` | Sreality v1 search API client |
| `src/parsers/idnes.py` | iDNES email + detail page parser |
| `src/region_filter.py` | Hard rules for every source (region, train, size, price, walls) |
| `src/locate.py` | Geocode + train distance for email listings |
| `src/transit.py` + `src/stations.json` | Offline nearest-station lookup (OSM snapshot) |
| `src/scorer.py` | Weighted scoring + categorized picks |
| `src/reporter.py` | Czech HTML report + scatter plot |
| `src/market_analysis.py` | One-time peer-group z-score analysis (not in daily flow; still reads removed greenery columns) |
| `.github/workflows/*.yml` | Daily + Tue/Fri runs; keepalive job in daily |

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

### 2026-11-25: Restart with narrowed search
- Workflows had been disabled for inactivity since 2026-06-02; default branch `devel` renamed `main`
- Sreality v2 API gone -> ported to v1 (still answers GitHub runners); greenery dropped; corridor Praha -> Úvaly/Nehvizdy
- iDNES kept via detail page + Nominatim + offline station list (public Overpass mirrors time out)
- Škvorec excluded after a viewing; other no-station towns (Jirny, Jenštejn, ...) kept by choice
- New master_v2.csv (old master.csv left on Drive); 510 listings after first runs (~420 Praha, ~90 corridor)
- Keepalive: third-party action blocked -> direct API call; reports now "since last sent report"
- PRs 1-3 merged; test report sent 25 Nov
