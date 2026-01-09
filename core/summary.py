# === core/summary.py ===
from __future__ import annotations

import sqlite3
from typing import Tuple

from bs4 import BeautifulSoup
import config
from utils.seen_filter import SeenFilter


def scrape_site_summary(
    driver,
    site_cfg,
    adapter,
    keyword_id: int,
    keyword: str,
    user_id: int,
    idx: int,
    total_keywords: int,
    *,
    seen: SeenFilter,
    db_path: str,
) -> Tuple[int, int]:
    """
    PASS 1: Perform summary scrape (listing cards). Skip already-seen listing_ids on sight (REQ-003).
    Insert minimal rows only for new items and return (inserted_count, total_reported_by_site).

    Skipping here reduces downstream work and lets us collect clean metrics:
      - inserted == number of genuinely new listings
      - total_reported is what the site advertised for this query
    """
    inserted = 0
    total_reported = 0

    url = adapter.build_search_url(site_cfg, keyword)
    driver.get(url)
    # Configurable wait; adapters may also implement fine-grained waits internally
    from time import sleep

    sleep(getattr(config, "WAIT_SEARCH_SEC", 2))
    html = driver.page_source

    soup = BeautifulSoup(html, "html.parser")
    cards = adapter.parse_summary_cards(soup)
    total_reported = adapter.parse_total_results(soup) or len(cards)

    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        for card in cards:
            listing_id = card.get("listing_id") or card.get("id")
            if not listing_id:
                continue

            # REQ-003: fast skip for already-seen listing_ids
            if seen.is_seen(site_id=site_cfg.site_id, listing_id=listing_id, keyword_id=keyword_id):
                continue

            ok = adapter.insert_minimal_listing(
                conn,
                user_id=user_id,
                site_id=site_cfg.site_id,
                keyword_id=keyword_id,
                listing_id=listing_id,
                title=card.get("title") or "",
                company=card.get("company") or "",
                location=card.get("location") or "",
                url=card.get("url") or "",
                base_score=getattr(config, "BASE_ENTRY_SCORE", 0),
            )
            if ok:
                inserted += 1
                # Mark as seen to avoid reprocessing within this run
                seen.mark_seen(site_id=site_cfg.site_id, listing_id=listing_id, keyword_id=keyword_id)

    return inserted, total_reported
