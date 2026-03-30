# Architectural Decisions

### 2026-03-30: IMAP/SMTP instead of Gmail API
- **Context**: Gmail API requires Google Cloud Console OAuth2 setup. User does not want to configure GCP on throwaway account.
- **Decision**: Use IMAP (read) + SMTP (send) with Gmail App Passwords. Standard Python `imaplib`/`smtplib`.
- **Alternatives**: Gmail API with OAuth2, third-party email services (SendGrid, Mailgun).
- **Outcome**: Zero external service dependencies for email. App password generated in 2 minutes.

### 2026-03-30: rclone for Google Drive sync
- **Context**: Google Drive API also requires GCP OAuth2. Need headless CI access (GitHub Actions).
- **Decision**: Use rclone with its built-in OAuth client. Config exported as base64 secret for CI.
- **Alternatives**: Google Drive API, Google Drive desktop app (local only), commit CSV to repo.
- **Outcome**: Drive sync works from both local and GitHub Actions. No GCP setup needed.

### 2026-03-30: Sreality JSON API instead of email parsing
- **Context**: Sreality alert emails only contain links to saved searches, not individual listings. The website is a React SPA (no server-rendered HTML).
- **Decision**: Extract saved search IDs from email, then query sreality internal JSON API (`/api/cs/v2/estates`) for actual listing data.
- **Alternatives**: Playwright/Selenium for SPA rendering, Sreality Premium for richer emails.
- **Outcome**: Rich structured data (GPS, labels, SEO slugs) without browser automation. URLs built from SEO fields.

### 2026-03-30: Categorized report picks instead of single top-N
- **Context**: Single composite top-10 biases toward one property profile. User wants to see cheap deals AND well-located expensive properties.
- **Decision**: Three report sections: (1) all above 85 composite, (2) top 5 by price/m2, (3) top 5 by location+greenery. Deduplicated across categories.
- **Alternatives**: Single ranked list, adjustable composite weights per report.
- **Outcome**: User sees 15+ unique properties covering different value propositions.

### 2026-03-31: Dual data flow -- email alerts + daily API scrape
- **Context**: Sreality `watchdog` param doesn't filter API results. Discovered the `/estates` endpoint accepts `locality_region_id` + `locality_district_id` for targeted queries. Full regional inventory (~4,200 filtered listings) available in 36s.
- **Decision**: Keep email alerts for new-deal notifications. Add daily full-region scrape (`scrape_region_listings`) for market context. Two complementary flows, not a replacement.
- **Alternatives**: Replace emails entirely with API scrape, keep email-only and fix watchdog query.
- **Outcome**: Email flow unchanged. Scrape runs after email parse in daily pipeline. Bulk data improves scoring baselines (price z-scores computed against 2,800+ listings vs 83).

### 2026-03-31: Region filtering -- text matching + GPS bounding box
- **Context**: Sreality API region filter doesn't catch all edge cases. iDNES/ceskereality have no API-level filtering. District field sometimes has town name instead of okres.
- **Decision**: Two-tier filter as post-parse safety net: (1) regex match on "Praha" + 3 target okres names, (2) GPS bounding box fallback (49.85-50.35N, 14.20-15.35E).
- **Alternatives**: Text-only filtering (misses towns without okres), GPS-only (needs coordinates), rely solely on portal-side filters.
- **Outcome**: Catches all sources. 13/4,375 sreality listings filtered (eastern Kolín fringe). GPS rescues towns like Český Brod, Brandýs nad Labem where district field lacks okres.

### 2026-03-31: OSM enrichment capped at 100 new records per run
- **Context**: Overpass API rate-limits to ~1 req/min under sustained load. Bulk enriching 4,200 listings would take 4+ hours. Previous 83-listing set worked fine.
- **Decision**: Cap OSM enrichment at 100 new records per `run_scrape()`. Keyword-based greenery + Praha heuristic transit scoring are sufficient baseline. OSM fills in incrementally over daily runs.
- **Alternatives**: Local Overpass instance, batch Overpass queries, skip OSM entirely, commercial geocoding API.
- **Outcome**: Daily pipeline runs within GH Actions 15-min timeout. Initial seed loaded without OSM. Enrichment backfills ~100 records/day.

### 2026-03-31: Peer-group z-scores for market analysis
- **Context**: With 2,800+ listings, absolute scoring puts all houses at the top (100 type + 80 greenery). Need relative comparison within segments.
- **Decision**: Standalone `market_analysis.py` with 4 peer groups (apt/house x prague/region). Z-scores for price, greenery, composite within each group. Not wired into daily email pipeline.
- **Alternatives**: Adjust absolute scoring weights, add peer group to daily reports, single composite ranking.
- **Outcome**: One-time PDF + CSV shortlist (50 picks) uploaded to Drive. Daily email reports remain simple absolute scoring -- appropriate for small batches of new listings.
