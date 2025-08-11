# REQUIREMENTS.MD

## 1) Project Overview

**Job Hunter** is a Python-based system that automates job search aggregation and tracking for two candidates, storing results in SQLite and presenting live dashboards via a Flask Blueprint web app. Repo primary languages: Python and HTML (approx. Python 78%, HTML 22%).

### Key goals
- Repeatedly scrape job listings for each **Site** using curated **Keywords** grouped under **Roles**.
- De-dupe and persist results in SQLite; skip previously seen `listing_id`s.
- Capture **Criteria** values to score suitability and support deep scanning of listings above a set threshold.
- Provide an admin dashboard to manage **Criteria**, **Roles** and **Keywords** (enable/disable, add), review metrics, and drill down into listings.
- Track scrape session progress in `scrape_progress.json` and show live progress UI.
- Partition the overall dataset via a **User** filter.
- Provide a listing summary and management dashboard able to action each listing as ignored/applied/pending.
- Provide a summary dashboard of total suitable jobs and quantity applied for by role, in a given period.

---

## 2) Architecture

### Components
- **Scrapers** (e.g., `seek_scraper_v3.py`)
  - Selenium + BeautifulSoup; two-pass (summary + deep scan).
  - Writes to SQLite (`job_hunt.db`) and progress JSON.
  - Respects Role/Keyword enable flags.
- **Dashboard (Flask, Blueprint)**
  - `dashboard.py` (app entry; registers blueprint)
  - `routes/main.py` (views: `/`, `/progress`, `/listings`, `/applications`, role/keyword actions)
  - `utils/db_helpers.py` (all DB access helpers; absolute path to DB)
  - `templates/` (`base.html`, `home.html`, `progress.html`, `listings.html`)
- **Database** (`job_hunt.db`)
  - See Data Model below.
- **Progress file**: `scrape_progress.json` (for live progress page)

### Execution flow
1. Load **active keywords** under **enabled roles** for the current **user**
2. Summary scrape by keyword: gather listing cards, create/update rows, count totals/skip duplicates, write progress JSON.
3. Assess properties of gathered listings against ceriteria sepcific to the current **user** and score suitability.
4. Deep scan: visit highly suitable items, enrich fields, re-score.
5. Dashboard reads DB + JSON to show totals, breakdowns, and progress.

---

## 3) Data Model (SQLite)

### Tables (minimum required)

- **Users**
  - `user_id` INTEGER PK
  - `user_name` TEXT NOT NULL

- **Job Sites**
  - `site_id` INTEGER PK
  - `site_name` TEXT NOT NULL
  - `url` TEXT
  - `url_prefix` TEXT
  - `url_suffix` TEXT
  - `tag_for_result_count` TEXT
  - `tag_for_cards` TEXT
  - `tag_for_location` TEXT
  - `tag_for_posted_on` TEXT

- **Criteria**
  - `criteria_id` INTEGER PK
  - `criteria_name` TEXT UNIQUE NOT NULL
  - `user_id` INTEGER REFERENCES Users(user_id) **ON DELETE CASCADE**
  - `listing_field` TEXT
  - `search_terms` TEXT

- **Roles**
  - `role_id` INTEGER PK
  - `role_name` TEXT UNIQUE NOT NULL
  - `user_id` INTEGER REFERENCES Users(user_id) **ON DELETE CASCADE**
  - `enabled` INTEGER DEFAULT 1
  - (*optional*) `rank` INTEGER for ordering

- **Keywords**
  - `keyword_id` INTEGER PK
  - `keyword` TEXT NOT NULL
  - `role_id` INTEGER REFERENCES Roles(role_id) **ON DELETE CASCADE** (recommended)
  - `enabled` INTEGER DEFAULT 1
  - `last_run` TEXT (ISO timestamp of last scrape for this keyword)

- **Job_Listings**
  - `listing_id` TEXT PK (seek ID)
  - `keyword_id` INTEGER REFERENCES Keywords(keyword_id) **ON DELETE CASCADE**
  - `title`, `company`, `location`, `url`
  - `listing_date` TEXT
  - `description` TEXT
  - `pay_rate` TEXT
  - `closing_date` TEXT
  - `work_schedule` TEXT (optional)
  - `experience_level` TEXT (optional)
  - `no_license` INTEGER, `no_experience` INTEGER
  - `suitability_score` INTEGER (1=not, 3=suitable, 5=high)
  - `status` TEXT DEFAULT `'new'` (`new|applied|ignored`)
  - `captured_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  - `site_id` INTEGER REFERENCES Sites(site_id)

- **Search_Run_Summary**
  - `run_id` INTEGER PK AUTOINCREMENT
  - `keyword_id` INTEGER REFERENCES Keywords(keyword_id)
  - `site_id` INTEGER REFERENCES Sites(site_id)
  - `listings_found` INTEGER
  - `skipped_duplicates` INTEGER
  - `run_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

- **Applications** (optional)
  - `listing_id` TEXT PK REFERENCES Job_Listings(listing_id)
  - `applied_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  - `method` TEXT (`listing|agency|company|email|government`)
  - `user_id` INTEGER REFERENCES Users(user_id) **ON DELETE CASCADE**

> Migration note: to enable cascade behavior for child deletes, rebuild child tables with `ON DELETE CASCADE`. Use a temporary table copy-and-rename approach with `PRAGMA foreign_keys=OFF/ON` during migration.

---

## 4) Dependencies

### Python
- **Flask** (web server & templates)
- **Jinja2** (templating; bundled with Flask)
- **Selenium** (browser automation)
- **BeautifulSoup4** (`bs4`) (HTML parsing)
- **tqdm** (CLI progress bars; optional with console UI)
- **pandas** (optional: exports/autosaves)
- **sqlite3** (stdlib)
- **requests** (optional for non-Selenium fetches)

### System
- **Chrome/Chromium** + matching **ChromeDriver**
- Windows or macOS/Linux shell
- Git (optional)

---

## 5) Environment & Configuration

- **Python**: 3.10+ recommended.
- **Virtual env** (recommended):
  ```bash
  python -m venv .venv
  .venv\Scripts\activate        # Windows
  # or
  source .venv/bin/activate     # macOS/Linux
  ```
- **Install deps**:
  ```bash
  pip install flask selenium beautifulsoup4 tqdm pandas
  ```
- **ChromeDriver**:
  - Ensure `chromedriver` is on PATH and matches your Chrome version.
- **Paths**:
  - In dashboard blueprints, **use absolute DB path** in `utils/db_helpers.py`:
    ```python
    import os
    DB_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../job_hunt.db'))
    ```

---

## 6) Running

### Dashboard (Blueprint)
```bash
cd dashboard_blueprint  # or your dashboard folder
python dashboard.py
# Visit http://127.0.0.1:5000
```

### Scraper
- With your orchestration script (e.g., `main_scraper.py`):
  1. For each **Site**
  2. Load active keywords under enabled roles:
     ```sql
     SELECT k.keyword_id, k.keyword
     FROM Keywords k
     JOIN Roles r ON k.role_id = r.role_id
	 JOIN Users u ON r.user_id = u.user_id
     WHERE r.enabled = 1 AND k.enabled = 1
     ORDER BY k.keyword;
     ```
  3. For each keyword, call `scrape_`+**site**+`(keyword_id, keyword, idx, total_keywords)`.
- Ensure **progress JSON** writes to `scrape_progress.json` for the dashboard `/progress` view.

---

## 7) Dashboard Endpoints (Blueprint)

- `GET /` — Home dashboard
  - Params: `user`, `site`, `added since`, `show disabled`
  - Role management (Add, Enable/Disable)
  - Keyword per-role management (Add, Enable/Disable)
  - Totals and per-role/keyword counts
- `GET /listings` — Filterable listings
  - Params: `user`, `site`, `role`, `keyword`, `suitability` (`not|mid|high`), `action`
  - Actions: Apply / Ignore / Pending
- `GET /progress` — Live progress page
  - Renders `scrape_progress.json`
- `GET /applications` - Summarised Actions
  - Params: `user`, `site`, `added since`
  - Totals and per-role/keyword counts

---

## 8) Scraper Requirements

- For each **Site** load the url prefix and suffix strings and tag values used the scrape the common information being captured

- **List Page Parsing**
  - Total listings: parse the element containing total counts (e.g., `Sites(tag_for_result_counts)`).
  - Listing cards: iterate `Sites(tag_for_cards)` tags; extract title, company, link, and location (use `Sites(tag_for_title)`, `Sites(tag_for_company)`, `Sites(tag_for_result_location)`).
- **De-duplication**
  - Consider an in-memory set of already-seen IDs from DB: `SELECT listing_id FROM Job_Listings WHERE site_id = Sites(site_id)`.
  - Skip inserting duplicates; count as `skipped_duplicates`.
- **Experience & Suitability**
  - Classify experience from title keywords; score suitability.
  - Deep scan only for highly suitable (score ≥ 4 or 5 depending on your scale).
- **Captcha Handling**
  - Pause and prompt manual resolution if “confirm you are human” detected.
- **Progress JSON**
  - Update after each page:
    ```json
    {
	  "site: Seek",
      "phase": "PASS 1|PASS 2",
      "keyword": "<current>",
      "keyword_index": 3,
      "total_keywords": 52,
      "processed_count": 220,
      "total_listings": 460,
      "not_suitable": 10,
      "suitable": 200,
      "highly_suitable": 10,
      "skipped_existing": 40,
      "deep_scanned": 4,
      "total_deep": 10
    }
    ```

---

## 9) Operational Conventions

- **Status values**: `new`, `applied`, `pending`, `ignored`
- **Suitability scale**: recommended {1=not, 3=suitable, 5=high}; keep consistent in all queries/UI.
- **Cascade deletes**:
  - `Users` → `Roles` and `Roles → Keywords` and `Keywords → Job_Listings` should include `ON DELETE CASCADE`.
- **Avoid hard deletes** for keywords/roles in normal ops; prefer toggling `enabled=0` to preserve history.

---

## 10) Windows Notes

- Use backslashes in paths for JSON patching (as per your automation).
- When running scripts from subfolders, ensure absolute DB path in `db_helpers.py` to avoid unintended duplicate DB files (e.g., `/dashboard/job_hunt.db` vs `/job_hunt.db`).

---

## 11) Code Editing Automation (JSON Patch Format)

Follow your **ChatGPT Code Editing Instructions**:
- Supply a single JSON object with:
  - `file`: backslash-separated relative path (e.g., `utils\\db_helpers.py`)
  - `edits`: array of change objects (`replace`, `insert_before`, `insert_after`, `replace_function`)
  - `commit_message`: concise description
- Escape quotes and represent newlines with `\n` only inside JSON; avoid escaping when writing actual files, templates, or code artifacts to disk to prevent `\\n`/`\"` artifacts.
- For **HTML templates**, avoid embedding as escaped strings; write as real multiline files.

---

## 12) Quality & Testing

- **Manual tests**:
  - Run scraper on a small subset of keywords.
  - Confirm: rows inserted, duplicates skipped, progress JSON updates.
- **Dashboard checks**:
  - Role/Keyword toggles update DB and filter scraping.
  - `/listings` filters/updates status correctly.
  - `/progress` reflects live JSON.
- **Log & debug**:
  - Add console logging around page loads, counts, and DB writes.

---

## 13) Future Enhancements

- Pagination & search on `/listings`
- Export CSV/Excel from dashboard
- Additional job sources (modular scraper interface)
- Scheduler (e.g., Windows Task Scheduler / cron)
- Auth for dashboard actions
- Retry/backoff on scraper failures

---

*Last updated: 9 Aug 2025 (AEST).*
