=== Job Hunter DB Migration: table prefixes (system_, config_, data_) ===

What changes?
-------------
Old names (mixed case / spaces) are renamed to clear, snake_case names:
    Users                -> system_users
    Sites                -> config_sites
    Keywords             -> data_keywords
    Applications         -> data_applications
    Roles                -> data_roles
    Criteria             -> config_criteria
    Criteria Lists       -> config_criteria_lists
    Job_Listings         -> data_job_listings
    Search_Run_Summary   -> data_search_run_summary

The "data_*" prefix cleanly separates data content from system/config tables.

How to run
----------
1) Dry run (prints what would happen, no changes):
   python migrate_names.py --db D:\Data\Code\Repos\job_hunter-dev\job_hunt.db --dry-run

2) Apply migration (creates .bak backup, renames, builds compatibility views, adds site_id indexes):
   python migrate_names.py --db D:\Data\Code\Repos\job_hunter-dev\job_hunt.db

   Options:
     --no-views    : don't create views with the old names
     --no-indexes  : don't add site_id indexes to data_* tables

Safety
------
- The script is idempotent: it only renames when the OLD exists and NEW doesn't.
- Identifiers are quoted (works even if old names have spaces).
- A timestamped .bak is created automatically (unless --dry-run).
- After commit, it runs PRAGMA foreign_key_check and reports violations.

Code changes (minimal)
----------------------
Add centralised names + quoting:

  from dashboard.db_names import T, qident

Then replace hard-coded table strings with T.*:
  "Job_Listings"       -> T.data_job_listings
  "Search_Run_Summary" -> T.data_search_run_summary
  "Sites"              -> T.config_sites
  ...etc...

Use qident() when composing SQL:
  sql = f"SELECT * FROM {qident(T.data_job_listings)} WHERE site_id=?"

Transitional compatibility
--------------------------
The migration (by default) creates VIEWS with the old names pointing at the new
tables, so the app can keep running while you change code. You can later drop
those views when you're confident the code uses the new names everywhere.

Rollback
--------
Use the .bak created alongside your DB. To reverse via script, swap the mapping
in migrate_names.py and run a dry-run first to verify.
