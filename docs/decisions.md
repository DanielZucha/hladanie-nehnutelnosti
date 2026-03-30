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
