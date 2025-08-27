**Title:** `[CRIT-P001] Duplicate Job Listings created`

**Summary**
- Expected: `REQ-002, REQ-010, REQ-043`
- Actual: Job Listing records are visible/created in the database that have the exact same Seek ID.
- Impact: Multiple copies of jobs cause the usability of the list to be heavily reduced and the tracking and statistics become meaningless.
- Environment: *(OS, browser, Python, DB path)*
- Evidence: 
SQL Query: SELECT * FROM Job_Listings WHERE listing_id = '86549092'
Result:

site_id	keyword_id	job_listing_id	listing_id	title	company	location	url	description	pay_rate	work_schedule	experience_level	listing_date	closing_date	no_license	no_experience	suitability_score	status	captured_at
1	31		86549092	Hospitality Assistant - Laundry	Bolton Clarke	Donvale	https://www.seek.com.au/job/86549092?type=standard&ref=search-standalone&origin=cardTitle#sol=6916064d3eac26468b5d3412d3a0aa719cb9b05f									4	new	2025-08-24 01:06:02
1	31		86549092	Hospitality Assistant - Laundry	Bolton Clarke	Donvale	https://www.seek.com.au/job/86549092?type=standard&ref=search-standalone&origin=cardTitle#sol=e4483f7b8f58c736940841085fb1bf8dffe8cb4c									4	new	2025-08-26 23:19:07

SQL Query: SELECT Count(*), listing_id FROM Job_Listings GROUP BY listing_id HAVING Count(*) > 1
Result:
Count(*)	listing_id
2	86270148
2	86300855
2	86332361
2	86382257
2	86539948
2	86549092
2	86566809
2	86630177
2	86636743

**Reproduction Steps**
1. Run `main_scraper.py` to completion.
2. At any later time, re-Run `main_scraper.py` to completion.
3. Review Job Listings data via UI or SQL back-end.

**Scope / Risk**
- Touched areas: Files within `\scapers` folder.
- Data risk: Minimal, all captured data can be regernated and is not in `live` use as yet.
- Rollback: Via GitHub if required.

**Priority & Severity**
- `Severity:` `Critical`
- `Priority:` `P1`

**Affected REQs**
- `REQ-002, REQ-010, REQ-021, REQ-041, REQ-042, REQ-043, REQ-047`