# scrapers/seek_scraper.py
# Thin wrapper around site_adapter for Seek-specific runs
from __future__ import annotations

import os, sys
from typing import List, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from scrapers.site_adapter import SiteConfig, SiteAdapter, make_driver, scrape_site_summary

try:
    from utils.db_helpers import get_active_keywords, DB_FILE
except Exception:
    DB_FILE = getattr(config, "DB_FILE", "job_hunt.db")
    def get_active_keywords(): return [(1, "Kitchen Hand"), (2, "Dishwasher")]

def scrape_seek_for_keywords(keywords: List[Tuple[int, str]]):
    cfg = SiteConfig(
        site_id=1,
        site_name="Seek",
        url="https://www.seek.com.au",
        tag_for_result_count='[data-automation="totalJobsMessage"]',
        tag_for_cards='article[data-automation="job-card"]',
        tag_for_title='a[data-automation="jobTitle"]',
        tag_for_company='[data-automation="jobCompany"]',
        tag_for_location='[data-automation="jobLocation"]',
    )
    adapter = SiteAdapter()
    driver = make_driver()
    try:
        total_inserted = 0
        for idx, (keyword_id, keyword) in enumerate(keywords, start=1):
            print(f"=== SEEK: {idx}/{len(keywords)} :: {keyword} ===")
            ins, reported = scrape_site_summary(
                driver, cfg, adapter,
                keyword_id=keyword_id, keyword=keyword,
                user_id=0, kw_index=idx, total_keywords=len(keywords),
            )
            print(f"Inserted {ins} of reported {reported} for keyword '{keyword}'\n")
            total_inserted += ins
        return total_inserted
    finally:
        if getattr(config, "KEEP_BROWSER_OPEN", False):
            print("KEEP_BROWSER_OPEN=True: leaving Chrome running.")
        else:
            driver.quit()

if __name__ == "__main__":
    kws = get_active_keywords()
    scrape_seek_for_keywords(kws)
