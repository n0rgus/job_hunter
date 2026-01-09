-- WARNING: Use only if you are sure the OLD tables exist and the NEW names do NOT exist.
-- Prefer the Python script (migrate_names.py) which is idempotent and safer.

PRAGMA foreign_keys=OFF;
BEGIN;

-- Quote legacy names that include spaces
ALTER TABLE "Users"              RENAME TO "system_users";
ALTER TABLE "Sites"              RENAME TO "config_sites";
ALTER TABLE "Keywords"           RENAME TO "data_keywords";
ALTER TABLE "Applications"       RENAME TO "data_applications";
ALTER TABLE "Roles"              RENAME TO "data_roles";
ALTER TABLE "Criteria"           RENAME TO "config_criteria";
ALTER TABLE "Criteria Lists"     RENAME TO "config_criteria_lists";
ALTER TABLE "Job_Listings"       RENAME TO "data_job_listings";
ALTER TABLE "Search_Run_Summary" RENAME TO "data_search_run_summary";

COMMIT;
PRAGMA foreign_keys=ON;

-- Optional: create compatibility views so old code keeps working temporarily
-- CREATE VIEW "Users"              AS SELECT * FROM "system_users";
-- CREATE VIEW "Sites"              AS SELECT * FROM "config_sites";
-- CREATE VIEW "Keywords"           AS SELECT * FROM "data_keywords";
-- CREATE VIEW "Applications"       AS SELECT * FROM "data_applications";
-- CREATE VIEW "Roles"              AS SELECT * FROM "data_roles";
-- CREATE VIEW "Criteria"           AS SELECT * FROM "config_criteria";
-- CREATE VIEW "Criteria Lists"     AS SELECT * FROM "config_criteria_lists";
-- CREATE VIEW "Job_Listings"       AS SELECT * FROM "data_job_listings";
-- CREATE VIEW "Search_Run_Summary" AS SELECT * FROM "data_search_run_summary";
