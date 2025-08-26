# REQUIREMENTS.MD (Annotated)

## 1) Project Overview

**Job Hunter** is a Python-based system that automates job search aggregation and tracking for two candidates, storing results in SQLite and presenting live dashboards via a Flask Blueprint web app.

### Key goals
- Repeatedly scrape job listings for each **Site** using curated **Keywords** grouped under **Roles**. **[REQ-001]**
- De-dupe and persist results in SQLite; skip previously seen `listing_id`s. **[REQ-002]**
- Capture **Criteria** values to score suitability and support deep scanning of listings above a set threshold. **[REQ-003]**
- Provide an admin dashboard to manage **Criteria**, **Roles** and **Keywords** (enable/disable, add), review metrics, and drill down into listings. **[REQ-004]**
- Track scrape session progress in `scrape_progress.json` and show live progress UI. **[REQ-005]**
- Partition the overall dataset via a **User** filter. **[REQ-006]**
- Provide a listing summary and management dashboard able to action each listing as ignored/applied/pending. **[REQ-007]**
- Provide a summary dashboard of total suitable jobs and quantity applied for by role, in a given period. **[REQ-008]**

---

## 2) Architecture

### Execution flow
1. Load **active keywords** under **enabled roles** for the current **user**. **[REQ-009]**
2. Summary scrape by keyword: gather listing cards, create/update rows, count totals/skip duplicates, write progress JSON. **[REQ-010]**
3. Assess properties of gathered listings against criteria specific to the current **user** and score suitability. **[REQ-011]**
4. Deep scan: visit highly suitable items, enrich fields, re-score. **[REQ-012]**
5. Dashboard reads DB + JSON to show totals, breakdowns, and progress. **[REQ-013]**

---

## 3) Data Model (SQLite)

- A relational SQLite database `job_hunt.db` SHALL be used as the system of record. **[REQ-014]**

### Tables (existence & key constraints)

- **Users**: create table with (`user_id` PK AI, `user_name` UNIQUE NOT NULL). **[REQ-015]**
- **Sites**: create table with (`site_id` PK AI, `site_name` UNIQUE NOT NULL, `url`, `url_prefix`, `url_suffix`, `tag_for_result_count`, `tag_for_cards`); remaining scraping tags moved to **Criteria** rows. **[REQ-016]**
- **Roles**: create table with (`role_id` PK AI, `role_name` UNIQUE NOT NULL, `user_id` FK, `site_id` FK, `rank`, `enabled` BOOLEAN DEFAULT 1) and enforce `ON DELETE CASCADE` for `user_id` and `site_id`. **[REQ-017]**
- **Keywords**: create table with (`keyword_id` PK AI, `role_id` FK, `keyword` NOT NULL, `enabled` BOOLEAN DEFAULT 1, `last_run`, `created_at` DEFAULT CURRENT_TIMESTAMP) with `ON DELETE CASCADE` from Roles. **[REQ-018]**
- **Criteria**: create table with (`criteria_id` PK, `user_id` FK, `site_id` FK, `criteria_field_name` UNIQUE NOT NULL, `tag`, `method`, `use_on_card_view` BOOLEAN, `maximum_score`, `increase_score`, `decrease_score`) allowing per-user/site overrides. **[REQ-019]**
- **Criteria Lists**: create table with (`item_id` PK, `criteria_id` FK, `list_item`, `impact_on_score` ENUM(minimum|decrease|increase|maximum)). **[REQ-020]**
- **Job_Listings**: create table with (`job_listing_id` INTEGER PK (AI), `listing_id` TEXT UNIQUE (site UID), `site_id` FK, `keyword_id` FK, fields: `title`, `company`, `location`, `url`, `description`, `pay_rate`, `work_schedule`, `experience_level`, dates, flags, `suitability_score`, `status` DEFAULT `new`, `captured_at` DEFAULT CURRENT_TIMESTAMP). **[REQ-021]**
- **Search_Run_Summary**: create table tracking per-run aggregates with PK `run_id` AUTOINCREMENT and timestamps. **[REQ-022]**
- **Applications (optional)**: create table with (`job_listing_id` FK/PK to Job_Listings, `user_id` FK, `applied_at` DEFAULT CURRENT_TIMESTAMP, `method`, `result`). **[REQ-023]**

**Migration**  
- When enabling cascade deletes on existing child tables, perform rebuild via temp tables with `PRAGMA foreign_keys=OFF/ON`. **[REQ-024]**

---

## 4) Dependencies

### Python
- Require: **Flask**, **Jinja2**, **Selenium**, **BeautifulSoup4**, **tqdm**, **pandas** (optional), **sqlite3** (stdlib), **requests** (optional). **[REQ-025]**

### System
- Chrome/Chromium with matching **ChromeDriver** available on PATH; Windows or macOS/Linux shell; Git optional. **[REQ-026]**

---

## 5) Environment & Configuration

- Use **Python 3.10+**. **[REQ-027]**
- Provide virtual environment setup instructions and support; venv recommended. **[REQ-028]**
- Provide install command for Python deps (`pip install flask selenium beautifulsoup4 tqdm pandas`). **[REQ-029]**
- Ensure ChromeDriver version matches installed Chrome and is resolvable on PATH. **[REQ-030]**
- In Flask blueprints, use **absolute** DB path (e.g., `utils/db_helpers.py` sets `DB_FILE = os.path.abspath(...)`). **[REQ-031]**

---

## 6) Running

### Dashboard (Blueprint)
- The dashboard app SHALL run via `python dashboard.py` and be reachable at `http://127.0.0.1:5000`. **[REQ-032]**

### Scraper
- Orchestrator (e.g., `main_scraper.py`) SHALL iterate **Sites**, load enabled **Keywords** under enabled **Roles**, and call site-specific scraper per keyword. **[REQ-033]**
- The provided SQL (JOIN Users/Roles/Keywords, filtered by `enabled`) or equivalent logic SHALL be used to source work items. **[REQ-034]**
- The scraper SHALL write `scrape_progress.json` as it runs for live UI consumption. **[REQ-035]**

---

## 7) Dashboard Endpoints (Blueprint)

- `GET /` — Home: supports params (`user`, `site`, `added since`, `show disabled`), role management (Add, Enable/Disable), and per-role/keyword totals. **[REQ-036]**
- `GET /listings` — Filterable listings by (`user`, `site`, `role`, `keyword`, `suitability`=`not|mid|high`, `action`) and supports actions: Apply / Ignore / Pending. **[REQ-037]**
- `GET /progress` — Renders `scrape_progress.json`. **[REQ-038]**
- `GET /applications` — Summarized actions with params (`user`, `site`, `added since`). **[REQ-039]**

---

## 8) Scraper Requirements

- For each **Site**, load `url_prefix`, `url_suffix`, and any tag selectors required to capture common fields. **[REQ-040]**

**List Page Parsing**
- Parse total listings using the element configured at `Sites(tag_for_result_count)`. **[REQ-041]**
- Iterate `Sites(tag_for_cards)` to extract: title, company, link, location, and posted date; use **Criteria(tag)** entries where `Criteria(use_on_card_view)=True`. **[REQ-042]**

**De-duplication**
- Maintain an in-memory set loaded from DB (`SELECT listing_id FROM Job_Listings WHERE site_id = ?`) and skip inserting duplicates; increment `skipped_duplicates`. **[REQ-043]**

**Suitability Score**
- Compute suitability by applying per-criteria modifiers from **Criteria**/**Criteria Lists** to a base score of 3 (scale of 1–5). **[REQ-044]**
- Perform **deep scan** only for highly suitable items (threshold ≥4 or 5, consistently applied). **[REQ-045]**

**Captcha Handling**
- Detect “confirm you are human”/CAPTCHAs and pause for manual resolution before continuing. **[REQ-046]**

**Progress JSON**
- After each page, update `scrape_progress.json` including fields like: `site`, `phase`, `keyword`, `keyword_index`, `total_keywords`, `processed_count`, `total_listings`, `not_suitable`, `highly_suitable`, `skipped_existing`, `deep_scanned`, `total_deep`. **[REQ-047]**

---

## 9) Operational Conventions

- Only these **status** values SHALL be used for listings: `new`, `applied`, `pending`, `ignored`. **[REQ-048]**
- Use a consistent **suitability** scale (recommended {1=not, 3=neutral, 5=high}) across DB, scoring, and UI. **[REQ-049]**
- Enforce cascade deletes for `Users → Roles`, `Roles → Keywords`, and `Keywords → Job_Listings`. **[REQ-050]**
- Prefer soft-disable (`enabled=0`) over hard deletes for Roles/Keywords to preserve history. **[REQ-051]**

---

## 10) Windows Notes

- Use backslashes in paths for JSON patching/automation (Windows semantics). **[REQ-052]**
- Ensure absolute DB paths in dashboard code to avoid unintended duplicate DB files under subfolders. **[REQ-053]**

---

## 11) Code Editing Automation (JSON Patch Format)

**DEFAULT → Unified Diff Patch**
- Respond to change requests with a unified “BEGIN/END FILE” patch per changed file containing full content; only include changed files. **[REQ-054]**
- Use `# GPT-ANCHOR:start/end:<name>` markers to allow surgical edits in large files. **[REQ-055]**

**FALLBACK → JSON edit plan**
- If patches aren’t feasible, accept a single JSON object with `file`, `edits` (`replace`, `insert_before`, `insert_after`, `replace_function`), and `commit_message`. **[REQ-056]**
- Do **not** escape real files/templates into strings; write proper multiline files (avoid `\n`/escaped quotes artifacts), especially for HTML templates. **[REQ-057]**

---

## 12) Quality & Testing

- Run manual tests on a small subset of keywords; verify: row inserts, duplicate skips, and progress JSON updates. **[REQ-058]**
- Verify dashboard behavior: role/keyword toggles affect scraping, `/listings` filters and updates status correctly, `/progress` reflects live JSON. **[REQ-059]**
- Add console logging around page loads, counts, and DB writes for operability/debug. **[REQ-060]**

---

## 13) Future Enhancements *(non-binding)*
- Add modular scrapers for additional job sources. **[FUT-001]**
