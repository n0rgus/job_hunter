from __future__ import annotations
import json
import os
import re
import sqlite3
import time
from typing import Dict, Any, List, Tuple

import config
from bs4 import BeautifulSoup

# Selenium setup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# Site abstraction
from scrapers.site_adapter import (
    load_sites,
    get_adapter_for,
    active_keywords_for_user,
    scrape_site_summary,
    SiteConfig,
)

# ---------- Selenium driver factory ----------
def make_driver() -> webdriver.Chrome:
    opts = Options()
    if config.HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    # Windows 'NUL' suppresses chromedriver logs; on *nix you can drop log_path.
    service = Service(log_path="NUL") if os.name == "nt" else Service()
    return webdriver.Chrome(options=opts, service=service)

# ---------- Helpers ----------
def save_progress(payload: Dict[str, Any]) -> None:
    # Ensure minimal fields exist to avoid KeyError in dashboard
    safe = {
        "site": payload.get("site", ""),
        "phase": payload.get("phase", "PASS 1"),
        "keyword": payload.get("keyword", ""),
        "keyword_index": int(payload.get("keyword_index", 0)),
        "total_keywords": int(payload.get("total_keywords", 0)),
        "processed_count": int(payload.get("processed_count", 0)),
        "total_listings": int(payload.get("total_listings", 0)),
        "not_suitable": int(payload.get("not_suitable", 0)),
        "suitable": int(payload.get("suitable", 0)),
        "highly_suitable": int(payload.get("highly_suitable", 0)),
        "skipped_existing": int(payload.get("skipped_existing", 0)),
        "deep_scanned": int(payload.get("deep_scanned", 0)),
        "total_deep": int(payload.get("total_deep", 0)),
    }
    with open(config.PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2)

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))

def load_criteria(conn, user_id=None, site_id=None):
    """
    Load card-view scoring criteria from DB tables:
      - Criteria (use_on_card_view = 1)
      - "Criteria Lists" (per-criterion items and impacts)

    Returns a list of dicts:
      [{
         'criteria_id': int,
         'field': str,            # resolved target field (e.g., 'title', 'company', 'location', 'url')
         'method': str,           # contains|equals|startswith|endswith|regex|word
         'max': Optional[float],
         'min': Optional[float],
         'inc': Optional[float],  # default +1.0 if not provided
         'dec': Optional[float],  # default -1.0 if not provided
         'items': [{ 'value': str, 'impact': str }...]
       }, ...]
    This structure mirrors what the scraper expects when applying preliminary (card-view) scoring.
    """
    import sqlite3

    def _to_float(val, default=None):
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        try:
            return float(s)
        except Exception:
            import re
            m = re.match(r'^[-+]?(\d+(?:\.\d+)?)$', s)
            if m:
                try:
                    return float(s)
                except Exception:
                    return default
        return default

    def _norm(s):
        return (s or "").strip().lower()

    def _resolve_target_field_name(criteria_field_name, tag):
        # Prefer explicit tag if present; otherwise use criteria_field_name
        name = _norm(tag) or _norm(criteria_field_name)
        aliases = {
            'job_title': 'title',
            'title': 'title',
            'company': 'company',
            'employer': 'company',
            'location': 'location',
            'suburb': 'location',
            'url': 'url',
            'link': 'url',
        }
        return aliases.get(name, name)

    rows = []
    try:
        conn.row_factory = None  # ensure tuple rows
        cur = conn.cursor()
        # Base query: only card-view criteria
        q = (
            'SELECT criteria_id, criteria_field_name, method, use_on_card_view, '
            '       maximum_score, increase_score, decrease_score, minimum_score, '
            '       COALESCE(tag, "") AS tag '
            'FROM Criteria '
            'WHERE use_on_card_view = 1'
        )
        params = []
        if site_id is not None:
            q += ' AND (site_id IS NULL OR site_id = ?)'
            params.append(site_id)
        if user_id is not None:
            q += ' AND (user_id IS NULL OR user_id = ?)'
            params.append(user_id)
        q += ' ORDER BY criteria_id'

        cur.execute(q, tuple(params))
        crit_rows = cur.fetchall() or []

        for (criteria_id, field_name, method, use_on_card_view, max_s, inc_s, dec_s, min_s, tag) in crit_rows:
            # Load list entries for this criterion
            cur.execute('SELECT item_id, list_item, impact_on_score FROM "Criteria Lists" WHERE criteria_id = ?', (criteria_id,))
            items = cur.fetchall() or []
            item_objs = [{ 'value': it[1], 'impact': it[2] } for it in items]

            rows.append({
                'criteria_id': criteria_id,
                'field': _resolve_target_field_name(field_name, tag),
                'method': method or 'contains',
                'max': _to_float(max_s, None),
                'min': _to_float(min_s, None),
                'inc': _to_float(inc_s, 1.0),
                'dec': _to_float(dec_s, -1.0),
                'items': item_objs,
            })

        return rows
    except sqlite3.OperationalError as e:
        # Tables/columns might not exist yet; fail gracefully
        print(f"[CRITERIA] load_criteria OperationalError: {e}")
        return []
    except Exception as e:
        print(f"[CRITERIA] load_criteria error: {e}")
        return []

def apply_criteria_score(base_score: int, criteria: List[Tuple[str, str]], listing: Dict[str, Any]) -> int:
    score = base_score
    for field, terms in criteria:
        if not field or not terms:
            continue
        field_val = (listing.get(field) or "").lower()
        if not field_val:
            continue
        for raw in [t.strip() for t in terms.split(",") if t.strip()]:
            neg = raw.startswith("!")
            term = raw[1:] if neg else raw
            if not term:
                continue
            if term.lower() in field_val:
                score += (config.CRITERIA_NEGATIVE_WEIGHT if neg else config.CRITERIA_POSITIVE_WEIGHT)
                score = clamp(score, config.MIN_SCORE, config.MAX_SCORE)
    return score

def write_run_summary(conn: sqlite3.Connection, keyword_id: int, site_id: int,
                      listings_found: int, skipped_duplicates: int) -> None:
    c = conn.cursor()
    c.execute("""
        INSERT INTO Search_Run_Summary (keyword_id, site_id, listings_found, skipped_duplicates)
        VALUES (?, ?, ?, ?)
    """, (keyword_id, site_id, listings_found, skipped_duplicates))
    conn.commit()

def select_for_deep_scan(conn: sqlite3.Connection, site_id: int, keyword_id: int,
                         threshold: int, limit: int) -> List[Tuple[str, str]]:
    c = conn.cursor()
    c.execute("""
        SELECT listing_id, url
        FROM Job_Listings
        WHERE site_id = ? AND keyword_id = ? AND suitability_score >= ?
          AND (description IS NULL OR description = '')
        ORDER BY captured_at DESC
        LIMIT ?
    """, (site_id, keyword_id, threshold, limit))
    return c.fetchall()

def update_listing_enrichment(conn: sqlite3.Connection, listing_id: str,
                              enrichment: Dict[str, Any], new_score: int) -> None:
    c = conn.cursor()
    c.execute("""
        UPDATE Job_Listings
        SET description = ?,
            pay_rate = ?,
            closing_date = ?,
            suitability_score = ?
        WHERE listing_id = ?
    """, (
        enrichment.get("description", ""),
        enrichment.get("pay_rate", ""),
        enrichment.get("closing_date", ""),
        new_score,
        listing_id,
    ))
    conn.commit()

def recalc_bucket_counts(conn: sqlite3.Connection, site_id: int, keyword_id: int) -> Dict[str, int]:
    c = conn.cursor()
    c.execute("""
        SELECT
          SUM(CASE WHEN suitability_score <= 1 THEN 1 ELSE 0 END),
          SUM(CASE WHEN suitability_score BETWEEN 2 AND 3 THEN 1 ELSE 0 END),
          SUM(CASE WHEN suitability_score >= 4 THEN 1 ELSE 0 END)
        FROM Job_Listings
        WHERE site_id = ? AND keyword_id = ?
    """, (site_id, keyword_id))
    row = c.fetchone() or (0, 0, 0)
    return {"not": row[0] or 0, "mid": row[1] or 0, "high": row[2] or 0}

# ---------- Main orchestration ----------
def main() -> None:
    driver = make_driver()
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")

            # Load sites from DB, filter if SITES_INCLUDE provided
            all_sites = load_sites(conn)
            if config.SITES_INCLUDE:
                sites = [s for s in all_sites if s.site_name in config.SITES_INCLUDE]
            else:
                sites = all_sites

            # Load active keywords for the current user (roles+keywords enabled)
            keywords = active_keywords_for_user(conn, config.USER_ID)
            if config.KEYWORD_LIMIT_PER_SITE:
                keywords = keywords[: config.KEYWORD_LIMIT_PER_SITE]
            total_keywords = len(keywords)

            # Preload criteria for scoring
            criteria = load_criteria(conn, config.USER_ID)

            # Loop sites
            for site_cfg in sites:
                # Normalize site_cfg to a SiteConfig when iterating a dict of sites (keys are ints)
                site_cfg = sites[site_cfg] if isinstance(site_cfg, int) else site_cfg
                adapter = get_adapter_for(site_cfg.site_name)
                site_label = site_cfg.site_name

                # Loop keywords
                for idx, (keyword_id, keyword) in enumerate(keywords, start=1):
                    # Initial progress reset per keyword
                    save_progress({
                        "site": site_label,
                        "phase": "PASS 1",
                        "keyword": keyword,
                        "keyword_index": idx,
                        "total_keywords": total_keywords,
                        "processed_count": 0,
                        "total_listings": 0,
                        "not_suitable": 0,
                        "suitable": 0,
                        "highly_suitable": 0,
                        "skipped_existing": 0,
                        "deep_scanned": 0,
                        "total_deep": 0,
                    })

                    # PASS 1: summary scrape (insert minimal rows, default/base score)
                    inserted, total_reported = scrape_site_summary(
                        driver, site_cfg, adapter, keyword_id, keyword,
                        config.USER_ID, idx, total_keywords
                    )

                    # Update counts & run summary
                    buckets = recalc_bucket_counts(conn, site_cfg.site_id, keyword_id)
                    write_run_summary(conn, keyword_id, site_cfg.site_id,
                                      listings_found=total_reported,
                                      skipped_duplicates=max(total_reported - inserted, 0))

                    save_progress({
                        "site": site_label,
                        "phase": "PASS 1",
                        "keyword": keyword,
                        "keyword_index": idx,
                        "total_keywords": total_keywords,
                        "processed_count": inserted,
                        "total_listings": total_reported,
                        "not_suitable": buckets["not"],
                        "suitable": buckets["mid"],
                        "highly_suitable": buckets["high"],
                        "skipped_existing": max(total_reported - inserted, 0),
                        "deep_scanned": 0,
                        "total_deep": 0,
                    })

                    # PASS 2: deep scan (optional)
                    if config.ENABLE_DEEP_SCAN and config.DEEP_SCAN_THRESHOLD is not None:
                        to_scan = select_for_deep_scan(
                            conn, site_cfg.site_id, keyword_id,
                            threshold=config.DEEP_SCAN_THRESHOLD,
                            limit=config.DEEP_SCAN_LIMIT_PER_KEYWORD
                        )
                        total_deep = len(to_scan)
                        deep_done = 0

                        for (listing_id, url) in to_scan:
                            save_progress({
                                "site": site_label,
                                "phase": "PASS 2",
                                "keyword": keyword,
                                "keyword_index": idx,
                                "total_keywords": total_keywords,
                                "processed_count": inserted,
                                "total_listings": total_reported,
                                "not_suitable": buckets["not"],
                                "suitable": buckets["mid"],
                                "highly_suitable": buckets["high"],
                                "skipped_existing": max(total_reported - inserted, 0),
                                "deep_scanned": deep_done,
                                "total_deep": total_deep,
                            })

                            # Load detail page and enrich
                            driver.get(url)
                            time.sleep(config.WAIT_JOB_SEC)
                            html = driver.page_source
                            enrichment = adapter.deep_enrich(site_cfg, html)

                            # Build a dict with fields for criteria matching
                            listing_for_scoring = {
                                "title": "", "description": "", "location": ""
                            }
                            # Pull minimal fields we may have stored already
                            c = conn.cursor()
                            c.execute("""
                                SELECT title, location, suitability_score
                                FROM Job_Listings
                                WHERE listing_id = ?
                            """, (listing_id,))
                            row = c.fetchone() or ("", "", config.BASE_ENTRY_SCORE)
                            listing_for_scoring["title"] = (row[0] or "")
                            listing_for_scoring["location"] = (row[1] or "")
                            listing_for_scoring["description"] = enrichment.get("description", "")

                            # Apply criteria scoring
                            base_score = row[2] or config.BASE_ENTRY_SCORE
                            new_score = apply_criteria_score(base_score, criteria, listing_for_scoring)

                            # Update DB
                            update_listing_enrichment(conn, listing_id, enrichment, new_score)

                            # Refresh buckets occasionally
                            deep_done += 1
                            if deep_done % 5 == 0 or deep_done == total_deep:
                                buckets = recalc_bucket_counts(conn, site_cfg.site_id, keyword_id)

                                save_progress({
                                    "site": site_label,
                                    "phase": "PASS 2",
                                    "keyword": keyword,
                                    "keyword_index": idx,
                                    "total_keywords": total_keywords,
                                    "processed_count": inserted,
                                    "total_listings": total_reported,
                                    "not_suitable": buckets["not"],
                                    "suitable": buckets["mid"],
                                    "highly_suitable": buckets["high"],
                                    "skipped_existing": max(total_reported - inserted, 0),
                                    "deep_scanned": deep_done,
                                    "total_deep": total_deep,
                                })

    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
