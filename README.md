# Louisville Democratic Party — 2026 Voter Guide

A Flask web application that generates a personalized sample ballot for Jefferson County, KY voters ahead of the **May 19, 2026 Primary Election**. Voters enter their street address, and the app identifies their precinct, displays every contested race on their ballot (with LDP endorsements highlighted), shows their Election Day polling place, and highlights the closest early voting site.

Live at: **sample-ballot-2026.louisvilledems.com**

---

## Features

### Address Lookup & Precinct Detection
- Voter enters a Louisville street address
- Geocoded using the **U.S. Census Bureau geocoder** (primary) with **ArcGIS** as a fallback — both free, no API key required
- **Point-in-polygon lookup** using `geopandas` against `precincts.geojson` (Jefferson County precinct shapefile) to identify the voter's precinct, Metro Council district, State House district, State Senate district, Congressional district, and Commissioner district

### Personalized Ballot
Races are shown in the same column order as the official Jefferson County ballot — partisan races first (left column), then nonpartisan (right column). Only contested races appear; uncontested races are omitted. LDP-endorsed candidates are highlighted in green.

**Partisan races (Democratic Primary):**
- United States Senator
- United States Representative in Congress — 2nd Congressional District *(CD-2 precincts only)*
- State Senator *(contested districts only)*
- State Representative *(contested districts only)*
- County Attorney
- County Clerk
- County Sheriff

**Nonpartisan races (all voters may participate):**
- Louisville Metro Mayor
- Louisville Metro Council *(odd-numbered districts with a primary only)*
- City of Shively — Mayor & Councilmember *(11 Shively precincts only)*
- City of Jeffersontown — Councilmember *(29 J-town precincts only)*

### Polling Place Lookup
After a successful address lookup, the app scrapes the **Jefferson County Clerk's "Where Do I Vote?" page** in real time to retrieve the voter's assigned Election Day polling place name and address. Results are cached in memory to avoid redundant scrapes. If the Clerk's site is unavailable, the app degrades gracefully without breaking the ballot display.

### Early Voting Locations
24 early voting sites (May 14–16, 2026 only) are listed after lookup. The **closest site is highlighted** using the Haversine distance formula against precomputed coordinates for all 24 locations — no runtime geocoding required. All other sites are available in a collapsible dropdown.

### Share / Save Ballot
Voters can capture their completed ballot as a PNG image using **html2canvas** and share or save it. Special handling is included for:
- **Facebook Messenger / Instagram in-app browsers** — bypasses broken `window.open()` by rendering an inline overlay with press-and-hold save instructions
- **Safari / mobile browsers** — same inline overlay approach using the Web Share API where available

### Volunteer Sign-Up
A modal form collects first name, last name, email, and phone number and posts to a **Google Apps Script webhook** that writes to a private Google Sheet.

---

## Architecture

```
sample-ballot-2026/
├── app.py                  # Flask backend
├── precincts.geojson       # Jefferson County precinct boundaries
├── templates/
│   └── index.html          # Single-page frontend (all JS inline)
├── static/
│   └── logo.jpg            # LDP logo
├── gunicorn.conf.py        # Production server config (Digital Ocean)
├── requirements.txt
├── discover_ballots.py     # One-time script: find all precinct ballot PDFs
├── parse_ballots.py        # One-time script: scrape PDFs for race titles
└── ballot_pdfs/            # Downloaded precinct PDFs (gitignored)
```

### Backend (`app.py`)
- **Flask** app loaded with the precinct GeoJSON at startup
- `/api/lookup` — geocodes an address, finds the precinct, scrapes the polling place, computes closest early voting site, returns JSON
- `/api/contact` — forwards volunteer sign-up data to the Google Sheets webhook
- Polling place results cached in `_polling_cache` (dict, in-memory, per process)

### Frontend (`templates/index.html`)
- Pure HTML/CSS/JS, no framework
- `buildBallot(data)` constructs the ballot HTML from candidate data arrays keyed by district
- Precinct-specific races (CD-2, Shively, Jeffersontown) gated by sets of precinct IDs derived from PDF content scans
- `selectCandidate()` supports both single-select (most races) and multi-select (Shively Council: up to 6, J-town Council: up to 8)
- Visual Viewport API used to keep the sign-up modal visible above the mobile keyboard in WebView browsers

---

## Precinct Research

### Ballot PDF Discovery
The Jefferson County Clerk publishes individual ballot PDFs for each precinct at:
```
https://jeffersoncountyclerk.org/wheredoivote/images/ballots/{PRECINCT}-D.pdf
```
`discover_ballots.py` iterates all 646 precincts and checks for a 200 response, finding **635 valid PDFs** (11 precincts had no PDF available).

### Finding Municipality-Specific Precincts
Some precincts overlay small incorporated cities (Jeffersontown, Shively) that have their own municipal elections on the same primary ballot, published at separate URLs:
```
https://jeffersoncountyclerk.org/wheredoivote/images/ballots/{PRECINCT}-JEF-D.pdf  # Jeffersontown
https://jeffersoncountyclerk.org/wheredoivote/images/ballots/{PRECINCT}-SHI-D.pdf  # Shively
```
Because the Clerk's server returns HTTP 200 for *all* precincts regardless of whether a city-specific ballot exists, URL pattern alone cannot identify which precincts belong to each city. Instead, `parse_ballots.py` downloads each PDF and uses **pdfplumber** to scan for city name text, identifying:
- **29 Jeffersontown precincts** (D and G prefix, plus several Q and V)
- **11 Shively precincts** (K and O prefix)

---

## Deployment

Hosted on **Digital Ocean** running Ubuntu. The app is served by **Gunicorn** behind Nginx.

```bash
# On the server
git pull
sudo systemctl restart sample-ballot
```

The `gunicorn.conf.py` binds to `0.0.0.0:5001` with `(cpu_count * 2 + 1)` workers.

---

## Candidate Data Sources

- **Candidates** verified against official Jefferson County ballot PDFs after the filing deadline (January 9, 2026)
- **Party registration** confirmed via Open Records Request
- **LDP endorsements** hardcoded in the `ENDORSEMENTS` object in `index.html`
- **Early voting locations** sourced from the Jefferson County Clerk's office announcement; coordinates precomputed via ArcGIS geocoder
