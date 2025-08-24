# scrapers/site_adapter.py
# SEEK adapter with pagination, robust extraction, DB-driven preliminary scoring,
# and detailed diagnostics routed to a log file (limited console output).
from __future__ import annotations

import os
import json
import sqlite3
import sys
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional, Union, Type

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# Ensure project root on sys.path so we can import config/utilities regardless of CWD
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
import config  # must define DB_FILE/DB_PATH and debug toggles

# Optional debug helpers (safe fallbacks if not present)
try:
    from scrapers.debug_tools import save_artifacts, highlight_cards, wait_for_results, slowmo
except Exception:
    def save_artifacts(*args, **kwargs):  # type: ignore
        pass
    def highlight_cards(*args, **kwargs):  # type: ignore
        return None
    def wait_for_results(*args, **kwargs):  # type: ignore
        pass
    def slowmo():  # type: ignore
        pass

# Optional DB helper imports (fallbacks provided)
try:
    from utils.db_helpers import upsert_listing_minimal, seen_ids_for_site  # type: ignore
    DB_FILE = getattr(config, "DB_FILE", getattr(config, "DB_PATH", "job_hunt.db"))
except Exception:
    DB_FILE = getattr(config, "DB_FILE", getattr(config, "DB_PATH", "job_hunt.db"))

    def upsert_listing_minimal(*args, **kwargs):  # type: ignore
        return False

    def seen_ids_for_site(*args, **kwargs):  # type: ignore
        return set()

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
import logging
from logging.handlers import RotatingFileHandler

LOGGER_NAME = "jobhunter"
LAST_SUMMARY: Dict[str, Any] = {}  # populated at end of scrape_site_summary

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def configure_logging(log_path: Optional[str] = None, level: int = logging.DEBUG) -> logging.Logger:
    """
    Configure the 'jobhunter' logger to write to a file with timestamps.
    No console handler is added here—main should control console output.
    Idempotent: safe to call multiple times.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if not log_path:
        logs_dir = getattr(config, "LOGS_DIR", os.path.join(ROOT_DIR, "logs"))
        _ensure_dir(logs_dir)
        log_path = os.path.join(logs_dir, "jobhunter.log")

    # Add rotating file handler once
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh.setFormatter(fmt)
        fh.setLevel(level)
        logger.addHandler(fh)

    return logger

# configure on import (file logger only)
logger = configure_logging()

def log_run_banner(start: bool, context: str = "") -> None:
    line = "=" * 100
    if start:
        logger.info(line)
        logger.info("▶▶ SCRAPE START %s", f"({context})" if context else "")
        logger.info(line)
    else:
        logger.info(line)
        logger.info("◀◀ SCRAPE END %s", f"({context})" if context else "")
        logger.info(line)

# -----------------------------------------------------------------------------
# SQL fallback upsert (for resilience across schema versions)
# -----------------------------------------------------------------------------
def _get_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]

def _direct_upsert_listing_row(
    listing_id: str,
    keyword_id: int,
    title: str,
    company: str,
    location: str,
    url: str,
    site_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Fallback upsert using sqlite directly. Auto-detects available columns in Job_Listings.
    Uses INSERT OR IGNORE (won't overwrite). Returns (inserted, message).
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("PRAGMA foreign_keys=ON")
        cols = _get_table_columns(conn, "Job_Listings")
        colset = set(c.lower() for c in cols)

        if "listing_id" not in colset or "keyword_id" not in colset:
            return False, f"Job_Listings missing required columns: have {cols}"

        insert_cols: List[str] = ["listing_id", "keyword_id", "title", "company", "location", "url"]
        values: List[Any] = [listing_id, keyword_id, title, company, location, url]

        if "site_id" in colset:
            insert_cols.append("site_id")
            values.append(site_id if site_id is not None else 1)
        if "status" in colset:
            insert_cols.append("status")
            values.append("new")

        has_captured_at = ("captured_at" in colset)
        placeholders = ",".join("?" for _ in insert_cols)
        col_sql = ",".join(insert_cols) + (",captured_at" if has_captured_at else "")
        values_sql_suffix = ",CURRENT_TIMESTAMP" if has_captured_at else ""
        sql = f"INSERT OR IGNORE INTO Job_Listings ({col_sql}) VALUES ({placeholders}{values_sql_suffix})"

        cur = conn.cursor()
        cur.execute(sql, values)
        conn.commit()
        inserted = cur.rowcount > 0
        if inserted:
            return True, "inserted"

        where = "listing_id = ?"
        params = [listing_id]
        if "site_id" in colset and site_id is not None:
            where += " AND site_id = ?"
            params.append(site_id)
        cur.execute(f"SELECT COUNT(1) FROM Job_Listings WHERE {where}", params)
        exists = cur.fetchone()[0] > 0
        if exists:
            return False, "ignored (duplicate)"
        return False, "ignored (unknown reason)"

    except sqlite3.IntegrityError as e:
        return False, f"IntegrityError: {e}"
    except Exception as e:
        return False, f"Exception: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

# -----------------------------------------------------------------------------
# Config dataclass and Adapters
# -----------------------------------------------------------------------------
@dataclass
class SiteConfig:
    site_id: int
    site_name: str
    url: str
    tag_for_result_count: Optional[str] = '[data-automation="totalJobsCountBcues"]'
    tag_for_cards: Optional[str] = 'article[data-automation="job-card"]'
    tag_for_title: Optional[str] = 'a[data-automation="jobTitle"]'
    tag_for_company: Optional[str] = '[data-automation="jobCompany"]'
    tag_for_location: Optional[str] = '[data-automation="jobLocation"]'

class SiteAdapter:
    """Base adapter. Subclass per-site if you need custom parsing/rules."""
    site_key: str = "generic"

    def build_search_url(self, cfg: SiteConfig, keyword: str, page: int) -> str:
        from urllib.parse import quote_plus
        return f"{cfg.url}/jobs?keywords={quote_plus(keyword)}&page={page}"

    def parse_listing_cards(self, cfg: SiteConfig, soup: BeautifulSoup) -> List[Any]:
        sel = cfg.tag_for_cards or 'article'
        return list(soup.select(sel))

    def parse_total_listings(self, cfg: SiteConfig, soup: BeautifulSoup) -> int:
        tag = soup.select_one('[data-automation="totalJobsCountBcues"]')
        if tag:
            m = re.search(r'(\d[\d,]*)', tag.get_text(' ', strip=True))
            if m:
                return int(m.group(1).replace(',', ''))
        tag = soup.select_one('[data-automation*="totalJobsCount"]')
        if tag:
            m = re.search(r'(\d[\d,]*)', tag.get_text(' ', strip=True))
            if m:
                return int(m.group(1).replace(',', ''))
        all_text = soup.get_text(' ', strip=True)
        m = re.search(r'\b(\d[\d,]*)\s+jobs\b', all_text, flags=re.IGNORECASE)
        if m:
            return int(m.group(1).replace(',', ''))
        try:
            if cfg.tag_for_cards:
                return len(soup.select(cfg.tag_for_cards))
        except Exception:
            pass
        return 0

    def extract_listing_minimal(self, cfg: SiteConfig, card: Any) -> Dict[str, Any]:
        """Extract minimal fields from a SERP card with defensive ID parsing."""
        def txt(el): return el.get_text(strip=True) if el else ''

        # Title / URL
        title_el = (card.select_one('a[data-automation="jobTitle"]')
                    or card.select_one('a[data-automation="job-card-title"]')
                    or card.select_one("a[href*='/job/']"))
        url_path = title_el.get('href') if title_el else ''
        if url_path and not (url_path.startswith('http') or url_path.startswith(cfg.url)):
            url = f"{cfg.url}{url_path}" if url_path.startswith('/') else f"{cfg.url}/{url_path}"
        else:
            url = url_path or ''

        # Company / Location
        company = txt(card.select_one('[data-automation="jobCompany"]')) or txt(card.select_one('[data-testid="company-name"]'))
        location = txt(card.select_one('[data-automation="jobLocation"]')) or txt(card.select_one('[data-testid="job-location"]'))
        title = txt(title_el)

        # Listing ID detection
        listing_id = None
        href = url or ''
        m = re.search(r"/job/(\d{5,12})\b", href)
        if m:
            listing_id = m.group(1)
        if not listing_id:
            try:
                listing_id = card.get('data-job-id') or None
            except Exception:
                listing_id = None
        if not listing_id:
            try:
                inner = str(card)
                m2 = re.search(r'"jobId"\s*:\s*(\d{5,12})', inner)
                if m2:
                    listing_id = m2.group(1)
            except Exception:
                pass
        if not listing_id and href:
            m3 = re.search(r"(\d{5,12})", href)
            if m3:
                listing_id = m3.group(1)

        logger.debug(f"[EXTRACT] title={title!r} company={company!r} location={location!r}")
        logger.debug(f"[EXTRACT] url={url!r} listing_id={listing_id!r}")

        return {
            'listing_id': listing_id,
            'title': title,
            'company': company,
            'location': location,
            'url': url,
        }

class SeekAdapter(SiteAdapter):
    """Seek adapter with driver-based parsing and robust card detection."""
    site_key: str = "seek"
    SELECTORS: Dict[str, Any] = {
        "total_message": '[data-automation="totalJobsMessage"]',
        "card_candidates": [
            'article[data-testid="job-card"]',
            'article[data-automation="normalJob"]',
            '[data-automation="job-card"] article',
            'article[id^="jobcard-"]',
        ],
    }

    def build_search_url(self, cfg: SiteConfig, keyword: str, page: int) -> str:
        kw = (keyword or '').strip()
        slug = re.sub(r'[^A-Za-z0-9]+', '-', kw).strip('-')
        location_slug = getattr(config, 'DEFAULT_LOCATION_SLUG', 'Ringwood-VIC-3134')
        distance_km = getattr(config, 'DEFAULT_DISTANCE_KM', 10)
        return f"{cfg.url}/{slug}-jobs/in-{location_slug}?distance={distance_km}&page={page}"

    def parse_total_listings(self, driver) -> int:
        """Return the integer total of listings using the live DOM."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # 1) Banner
        try:
            WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, self.SELECTORS["total_message"]))
            )
            el = driver.find_element(By.CSS_SELECTOR, self.SELECTORS["total_message"])
            txt = (el.text or "").strip()
            m = re.search(r'(\d[\d,]*)', txt)
            if m:
                total = int(m.group(1).replace(',', ''))
                logger.debug(f"[SeekAdapter] totalJobsMessage='{txt}' -> total={total}")
                return total
        except Exception:
            pass

        # 2) Meta description
        try:
            metas = driver.find_elements(By.CSS_SELECTOR, 'meta[name="description"]')
            for md in metas:
                content = (md.get_attribute("content") or "").strip()
                m = re.search(r'with\s+(\d[\d,]*)\s+jobs?\s+found', content, re.IGNORECASE)
                if m:
                    total = int(m.group(1).replace(',', ''))
                    logger.debug(f"[SeekAdapter] meta description -> total={total}")
                    return total
        except Exception:
            pass

        # 3) Embedded JSON totalJobCount
        try:
            html = driver.page_source or ""
            m = re.search(r'"totalJobCount"\s*:\s*(\d+)', html)
            if m:
                total = int(m.group(1))
                logger.debug(f"[SeekAdapter] embedded totalJobCount -> total={total}")
                return total
        except Exception:
            pass

        logger.debug("[SeekAdapter] WARNING: total listings not found; defaulting to 0")
        return 0

    def discover_page_size(self, driver) -> int:
        """Discover page size from embedded JSON; fallback to 22."""
        try:
            html = driver.page_source or ""
            m = re.search(r'"pageSize"\s*:\s*(\d+)', html)
            if m:
                page_size = int(m.group(1))
                logger.debug(f"[SeekAdapter] pageSize discovered -> {page_size}")
                return page_size
        except Exception:
            pass
        logger.debug("[SeekAdapter] pageSize not found; defaulting to 22")
        return 22

    def get_job_card_elements(self, driver):
        """Robustly locate job cards on SEEK and outline if HIGHLIGHT_UI is True."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        try:
            WebDriverWait(driver, 6).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, self.SELECTORS.get("total_message", '[data-automation="totalJobsMessage"]'))
                )
            )
        except Exception:
            pass

        candidates = self.SELECTORS.get("card_candidates", [
            'article[data-testid="job-card"]',
            'article[data-automation="normalJob"]',
            '[data-automation="job-card"] article',
            'article[id^="jobcard-"]',
        ])

        for css in candidates:
            try:
                WebDriverWait(driver, 3).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, css))
                )
                elems = driver.find_elements(By.CSS_SELECTOR, css)
                if elems:
                    if getattr(config, 'HIGHLIGHT_UI', True):
                        driver.execute_script(
                            "document.querySelectorAll(arguments[0]).forEach((n)=>{n.style.outline='3px solid #ff0066';n.style.outlineOffset='2px';});",
                            css
                        )
                    logger.debug(f"[SeekAdapter] job-card selector matched: '{css}' -> {len(elems)} on page")
                    return elems
            except Exception:
                pass

        # JS discovery (diagnostic)
        combined = ",".join(candidates)
        try:
            if getattr(config, 'HIGHLIGHT_UI', True):
                driver.execute_script(
                    "var sel=arguments[0]; document.querySelectorAll(sel).forEach(n=>{n.style.outline='3px dashed #ff9900';n.style.outlineOffset='2px';});",
                    combined
                )
            count = driver.execute_script("return document.querySelectorAll(arguments[0]).length;", combined)
            logger.debug(f"[SeekAdapter] JS discovery: '{combined}' -> {count} candidates (Selenium none)")
        except Exception:
            pass

        return []

# -----------------------------------------------------------------------------
# Selenium driver
# -----------------------------------------------------------------------------
def make_driver() -> webdriver.Chrome:
    opts = Options()
    show = getattr(config, 'DEBUG_VISUAL', True)
    if not show and getattr(config, 'HEADLESS', False):
        # Some sites detect new headless; you can toggle HEADLESS False in config if needed.
        opts.add_argument('--headless=new')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument('--disable-infobars')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--no-first-run')
    opts.add_argument('--no-default-browser-check')
    opts.add_argument('--disable-extensions')

    if getattr(config, 'BLOCK_IMAGES', False):
        prefs = {"profile.managed_default_content_settings.images": 2}
        opts.add_experimental_option("prefs", prefs)

    vw, vh = getattr(config, 'VIEWPORT', (1400, 1000))
    opts.add_argument(f'--window-size={vw},{vh}')
    opts.add_experimental_option('detach', True)
    opts.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    )

    driver = webdriver.Chrome(options=opts)
    if hasattr(driver, "set_page_load_timeout"):
        driver.set_page_load_timeout(int(getattr(config, "PAGE_LOAD_TIMEOUT", 25)))
    return driver

# -----------------------------------------------------------------------------
# Criteria loading & preliminary scoring (card-view)
# -----------------------------------------------------------------------------
def _to_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    try:
        return float(s)
    except Exception:
        m = re.match(r'^[-+]?(\d+(?:\.\d+)?)$', s)
        if m:
            try:
                return float(s)
            except Exception:
                return default
    return default

def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _truthy(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    vs = str(v).strip().lower()
    return vs in ('1', 'true', 't', 'yes', 'y', 'on')

def _resolve_target_field_name(criteria_field_name: str, tag: Optional[str]) -> str:
    """
    Normalize a criteria target to one of: 'title', 'company', 'location', 'url'.
    Accepts aliases and CSS-like selectors seen in imported criteria (e.g.,
    "a[data-automation=jobtitle]") and maps them sensibly.
    """
    raw = (tag or criteria_field_name or '').strip()
    low = raw.lower()

    # Explicit aliases
    alias_map = {
        'job_title': 'title',
        'title': 'title',
        'company': 'company',
        'employer': 'company',
        'location': 'location',
        'suburb': 'location',
        'url': 'url',
        'link': 'url',
    }
    if low in alias_map:
        return alias_map[low]

    # Heuristics for CSS-like selectors or verbose tokens
    if ('jobtitle' in low) or ('data-automation=jobtitle' in low) or ('job-title' in low) or ('job_title' in low):
        return 'title'
    if ('jobcompany' in low) or ('data-automation=jobcompany' in low) or ('company' in low):
        return 'company'
    if ('joblocation' in low) or ('data-automation=joblocation' in low) or ('location' in low) or ('suburb' in low):
        return 'location'

    # If it *looks* like a selector, default to title for card-view relevance
    if any(ch in low for ch in ['[',']','=', '#','.', ' ']):
        return 'title'

    # Fallback: return the lower-cased token (may match a custom listing field)
    return low

def _load_card_criteria(site_id: int, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Load DB criteria for card-view preliminary scoring. We fetch rows and then
    filter use_on_card_view with a robust truthy check to tolerate SQLite booleans
    stored as ints or strings.
    """
    rows: List[Dict[str, Any]] = []
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        q = (
            'SELECT criteria_id, criteria_field_name, method, use_on_card_view, '
            '       maximum_score, increase_score, decrease_score, minimum_score, '
            '       COALESCE(tag, "") AS tag, COALESCE(site_id, -1) AS site_id, COALESCE(user_id, -1) AS user_id '
            'FROM Criteria'
        )
        params: List[Any] = []
        conds: List[str] = []
        if site_id is not None:
            conds.append('(site_id IS NULL OR site_id = ?)')
            params.append(site_id)
        if user_id is not None:
            conds.append('(user_id IS NULL OR user_id = ?)')
            params.append(user_id)
        if conds:
            q += ' WHERE ' + ' AND '.join(conds)
        q += ' ORDER BY criteria_id'

        cur.execute(q, params)
        crit_rows = cur.fetchall() or []

        kept = 0
        for cr in crit_rows:
            if not _truthy(cr['use_on_card_view']):
                continue
            cid = cr['criteria_id']

            cur.execute('SELECT item_id, list_item, impact_on_score FROM "Criteria Lists" WHERE criteria_id = ?', (cid,))
            items = cur.fetchall() or []
            item_objs = [{'value': it['list_item'], 'impact': it['impact_on_score'] } for it in items]

            rows.append({
                'criteria_id': cid,
                'field': _resolve_target_field_name(cr['criteria_field_name'], cr['tag']),
                'method': cr['method'] or 'contains',
                'max': _to_float(cr['maximum_score'], None),
                'min': _to_float(cr['minimum_score'], None),
                'inc': _to_float(cr['increase_score'], 1.0),
                'dec': _to_float(cr['decrease_score'], -1.0),
                'items': item_objs,
            })
            kept += 1

        logger.debug(f"[CRITERIA] fetched rows={len(crit_rows)}, card-view kept={kept}")
        if kept:
            try:
                sample = rows[:2]
                dumped = json.dumps(sample, ensure_ascii=False)
                logger.debug("[CRITERIA] sample: %s", dumped[:500] + ("…" if len(dumped) > 500 else ""))
            except Exception:
                pass

        conn.close()
    except Exception as e:
        logger.debug(f"[CRITERIA] load error: {e}")
    return rows

def _apply_card_scoring(listing: Dict[str, Any], criteria_rows: List[Dict[str, Any]], base_score: int = 3) -> Tuple[int, bool, List[str]]:
    """
    Apply card-view criteria to listing fields (title/company/location/url).
    Returns (score, excluded, reasons[]).

    Adds detailed DEBUG logging for inputs, per-match (optional), and outputs.
    Toggle per-match logs with config.LOG_SCORING_MATCHES = True.

    NOTE on bounds: "minimum" is applied as a *cap* (downward ceiling),
    and "maximum" is applied as a *floor* (upward floor) AFTER all deltas.
    """
    import json as _json
    import re as _re

    log_matches = bool(getattr(config, 'LOG_SCORING_MATCHES', False))

    # ---- Input logging ----
    lid = listing.get('listing_id') if isinstance(listing, dict) else None
    try:
        logger.debug(
            f"[SCORING-IN] listing_id={lid!r} title={listing.get('title','')!r} company={listing.get('company','')!r} "
            f"location={listing.get('location','')!r} url={listing.get('url','')!r}"
        )
        logger.debug(f"[SCORING-IN] criteria_count={len(criteria_rows)} base_score={base_score}")
        if log_matches and criteria_rows:
            sample = [{k: v for k, v in c.items() if k in ('criteria_id','field','method','min','max','inc','dec','items')} for c in criteria_rows[:2]]
            try:
                logger.debug('[SCORING-IN] criteria_sample: ' + _json.dumps(sample, ensure_ascii=False)[:600])
            except Exception:
                pass
    except Exception:
        pass

    # ---- Helpers ----
    def field_value(name: str) -> str:
        n = (name or '').strip().lower()
        if n == 'title':
            return listing.get('title') or ''
        if n == 'company':
            return listing.get('company') or ''
        if n in ('location', 'suburb'):
            return listing.get('location') or ''
        if n in ('url', 'link'):
            return listing.get('url') or ''
        return listing.get(n, '') or ''

    def _to_float_local(val, default=None):
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        try:
            return float(s)
        except Exception:
            m = _re.match(r'^[-+]?(\\d+(?:\\.\\d+)?)$', s)
            return float(s) if m else default

    def _norm(s):
        return (s or '').strip().lower()

    def _match(method: str, haystack: str, needle: str) -> bool:
        m = _norm(method)
        h = (haystack or '').lower()
        n = (needle or '').lower()
        try:
            if m in ('equals', 'exact'):
                return h == n
            if m == 'startswith':
                return h.startswith(n)
            if m == 'endswith':
                return h.endswith(n)
            if m in ('regex', 're'):
                return _re.search(needle, haystack, _re.IGNORECASE) is not None
            if m in ('word', 'wholeword'):
                return _re.search(rf"\b{_re.escape(n)}\b", h) is not None
            # Treat unknowns and 'list', 'any', 'range' as substring contains
            return n in h
        except Exception:
            return n in h

    # ---- Scoring ----
    score = float(base_score)
    excluded = False
    reasons: List[str] = []

    # Global post-adjust caps that take priority *after* increments/decrements
    enforce_min_after: Optional[float] = None  # downward cap
    enforce_max_after: Optional[float] = None  # upward floor

    for c in criteria_rows:
        # Normalize method synonyms (e.g., 'List', 'Any', 'Range' -> 'contains')
        raw_method = c.get('method') or 'contains'
        method = _norm(raw_method)
        if method in ('list', 'any', 'contains_any', 'include', 'range'):
            method = 'contains'

        fval = field_value(c.get('field', ''))
        if log_matches:
            logger.debug(f"[SCORING-FIELD] field={c.get('field')} method={method} haystack={fval!r}")

        local_min = c.get('min')
        local_max = c.get('max')
        inc = c.get('inc', 1.0)
        dec = c.get('dec', -1.0)

        for it in (c.get('items', []) or []):
            value = it.get('value') or ''
            impact_raw = it.get('impact')
            if not value:
                continue

            # Normalize impact synonyms
            impact_alias = _norm(impact_raw).replace('_', '') if impact_raw is not None else ''
            if impact_alias in ('minimumscore', 'minscore', 'minimum', 'min'):
                impact = 'minimum'
            elif impact_alias in ('maximumscore', 'maxscore', 'maximum', 'max'):
                impact = 'maximum'
            elif impact_alias in ('increasescore', 'increase', 'inc', 'positive', 'include'):
                impact = 'increase'
            elif impact_alias in ('decreasescore', 'decrease', 'dec', 'negative'):
                impact = 'decrease'
            elif impact_alias in ('exclude', 'ban', 'block'):
                impact = 'exclude'
            else:
                impact = impact_alias  # may be numeric like '+2' or '1.5'

            matched = _match(method, fval, value)
            if log_matches:
                logger.debug(
                    f"[SCORING-TRY] field={c.get('field')} method={method} value={value!r} haystack={fval!r} matched={matched}"
                )
            if not matched:
                continue

            # Numeric delta?
            num = _to_float_local(impact, None)
            if num is not None:
                before = score
                score += num
                reasons.append(f"{c['field']}:{value}:+{num:g}")
                if log_matches:
                    logger.debug(f"[SCORING-HIT] +{num:g} | {before:g} => {score:g}")
            else:
                if impact == 'increase':
                    delta = inc if inc is not None else 1.0
                    before = score
                    score += float(delta)
                    reasons.append(f"{c['field']}:{value}:inc{delta}")
                    if log_matches:
                        logger.debug(f"[SCORING-HIT] inc{delta} | {before:g} => {score:g}")
                elif impact == 'decrease':
                    delta = dec if dec is not None else -1.0
                    before = score
                    score += float(delta)
                    reasons.append(f"{c['field']}:{value}:dec{delta}")
                    if log_matches:
                        logger.debug(f"[SCORING-HIT] dec{delta} | {before:g} => {score:g}")
                elif impact == 'exclude':
                    excluded = True
                    reasons.append(f"{c['field']}:{value}:EXCLUDE")
                    if log_matches:
                        logger.debug(f"[SCORING-HIT] EXCLUDE matched for value={value!r}")
                elif impact == 'minimum':
                    # Apply as a downward cap *after* all adjustments
                    if local_min is not None:
                        enforce_min_after = local_min if (enforce_min_after is None) else min(enforce_min_after, local_min)
                        reasons.append(f"{c['field']}:{value}:min_cap->{local_min}")
                elif impact == 'maximum':
                    # Apply as an upward floor *after* all adjustments
                    if local_max is not None:
                        enforce_max_after = local_max if (enforce_max_after is None) else max(enforce_max_after, local_max)
                        reasons.append(f"{c['field']}:{value}:max_floor->{local_max}")
                else:
                    reasons.append(f"{c['field']}:{value}:noop({impact})")
                    if log_matches:
                        logger.debug(f"[SCORING-HIT] noop impact={impact!r}")

        # No immediate bound enforcement here — bounds applied after the loop.

    # Global post-adjust bounds, applied in order: cap-down, then floor-up
    if enforce_min_after is not None:
        before = score
        score = min(score, float(enforce_min_after))
        if log_matches:
            logger.debug(f"[SCORING-POST] apply min_cap {enforce_min_after} | {before:g} => {score:g}")
    if enforce_max_after is not None:
        before = score
        score = max(score, float(enforce_max_after))
        if log_matches:
            logger.debug(f"[SCORING-POST] apply max_floor {enforce_max_after} | {before:g} => {score:g}")

    final_score = int(round(score))

    # ---- Output logging ----
    try:
        logger.debug(
            f"[SCORING-OUT] listing_id={lid!r} score={final_score} excluded={excluded} reasons={reasons}"
        )
    except Exception:
        pass

    return final_score, excluded, reasons

def _update_listing_score(listing_id: str, site_id: int, score: int, queue_deep: bool) -> None:
    """Update suitability_score (and optionally status) for a listing."""
    try:
        from utils.db_helpers import update_listing_score as _upd  # type: ignore
        _upd(listing_id, site_id, score, queue_deep)
        return
    except Exception:
        pass
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("PRAGMA foreign_keys=ON")
        cur = conn.cursor()
        cols = [r[1].lower() for r in cur.execute("PRAGMA table_info(Job_Listings)").fetchall()]
        has_site = 'site_id' in cols
        has_status = 'status' in cols
        if has_site and has_status:
            cur.execute(
                "UPDATE Job_Listings SET suitability_score=?, status=COALESCE(status, ?) WHERE listing_id=? AND site_id=?",
                (score, ('queued_deep' if queue_deep else 'new'), listing_id, site_id)
            )
        elif has_site:
            cur.execute("UPDATE Job_Listings SET suitability_score=? WHERE listing_id=? AND site_id=?",
                        (score, listing_id, site_id))
        elif has_status:
            cur.execute("UPDATE Job_Listings SET suitability_score=?, status=COALESCE(status, ?) WHERE listing_id=?",
                        (score, 'queued_deep' if queue_deep else 'new', listing_id))
        else:
            cur.execute("UPDATE Job_Listings SET suitability_score=? WHERE listing_id=?",
                        (score, listing_id))
        conn.commit()
    except Exception as e:
        logger.debug(f"[SCORING] update failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

# -----------------------------------------------------------------------------
# Summary scraper (pagination, upsert, scoring) — returns (inserted, total_reported)
# -----------------------------------------------------------------------------
def scrape_site_summary(driver, cfg: SiteConfig, adapter: SiteAdapter,
                        keyword_id: int, keyword: str,
                        user_id: int, kw_index: int, total_keywords: int) -> Tuple[int, int]:
    """
    Scrape all pages for a given keyword on a site.
    Returns (inserted_count, total_reported).
    Computes preliminary score *before* DB upsert, passes listing_id to the scorer,
    and applies global min/max caps after increments/decrements.
    """
    from selenium.webdriver.common.by import By

    log_run_banner(True, f"{cfg.site_name} | kw[{kw_index}/{total_keywords}] '{keyword}'")

    inserted = 0
    skipped_existing = 0
    scored_not = 0
    scored_mid = 0
    scored_high = 0

    # Load criteria once
    criteria_rows = _load_card_criteria(cfg.site_id, user_id=user_id)
    provisional_threshold = int(getattr(config, 'PROVISIONAL_THRESHOLD', 3))

    total_sel = getattr(adapter, 'SELECTORS', {}).get('total_message', '[data-automation="totalJobsMessage"]')
    card_candidates = getattr(adapter, 'SELECTORS', {}).get('card_candidates', [
        'article[data-testid="job-card"]',
        'article[data-automation="normalJob"]',
        '[data-automation="job-card"] article',
        'article[id^="jobcard-"]',
    ])
    wait_timeout = max(2, int(getattr(config, 'WAIT_FOR_RESULTS_TIMEOUT', 10)))
    settle_delay = float(getattr(config, 'PAGE_SETTLE_DELAY', 0.2))

    def _any_cards_present() -> bool:
        for css in card_candidates:
            try:
                if driver.find_elements(By.CSS_SELECTOR, css):
                    return True
            except Exception:
                pass
        return False

    # Page 1
    page = 1
    url = adapter.build_search_url(cfg, keyword, page)
    logger.debug(f"[NAV] {cfg.site_name} -> {url}")
    driver.get(url)
    slowmo()

    t0 = time.time()
    while True:
        try:
            if driver.find_elements(By.CSS_SELECTOR, total_sel):
                break
        except Exception:
            pass
        if _any_cards_present():
            break
        if time.time() - t0 > wait_timeout:
            logger.debug(f"[WAIT] timed out after {wait_timeout}s waiting for results")
            break
        time.sleep(0.2)
    time.sleep(settle_delay)

    cur_url = getattr(driver, 'current_url', '') or ''
    html = driver.page_source or ''
    if cur_url.startswith('data:') or len(html.strip()) < 200:
        logger.debug("[WARN] Page appears blank; retry once.")
        if getattr(config, 'SAVE_ARTIFACTS', True):
            save_artifacts(driver, cfg.site_name, keyword, f"p{page:02d}_blank_initial")
        driver.get(url)
        slowmo()
        t0 = time.time()
        while True:
            try:
                if driver.find_elements(By.CSS_SELECTOR, total_sel):
                    break
            except Exception:
                pass
            if _any_cards_present():
                break
            if time.time() - t0 > wait_timeout:
                break
            time.sleep(0.2)
        cur_url = getattr(driver, 'current_url', '') or ''
        html = driver.page_source or ''
        if cur_url.startswith('data:') or len(html.strip()) < 200:
            logger.debug("[ERROR] Still blank after retry. Aborting keyword.")
            if getattr(config, 'SAVE_ARTIFACTS', True):
                save_artifacts(driver, cfg.site_name, keyword, f"p{page:02d}_blank_retry")
            LAST_SUMMARY.update({
                'keyword': keyword, 'site': cfg.site_name,
                'inserted': 0, 'skipped_existing': 0, 'total_reported': 0,
                'scored': {'not': 0, 'mid': 0, 'high': 0}
            })
            log_run_banner(False, f"{cfg.site_name} | kw[{kw_index}/{total_keywords}] '{keyword}'")
            return 0, 0

    # Total reported
    badge_text = None
    try:
        badge_text = driver.execute_script(
            "var a=document.querySelector(arguments[0]); return a? a.innerText : null;",
            total_sel
        )
        logger.debug(f"[DEBUG] total_jobs_banner_text: {badge_text!r}")
    except Exception:
        pass

    total_reported = 0
    try:
        meth = getattr(adapter, 'parse_total_listings', None)
        if callable(meth):
            import inspect
            params = list(inspect.signature(meth).parameters.keys())
            if 'soup' in params or len(params) >= 3:
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                total_reported = adapter.parse_total_listings(cfg, soup)  # type: ignore[arg-type]
            else:
                total_reported = adapter.parse_total_listings(driver)  # type: ignore[arg-type]
    except Exception as e:
        logger.debug(f"[DEBUG] parse_total_listings failed: {e}")
        total_reported = 0
    if (not total_reported) and badge_text:
        m = re.search(r'(\d[\d,]*)', badge_text or '')
        if m:
            try:
                total_reported = int(m.group(1).replace(',', ''))
                logger.debug(f"[DEBUG] total_reported fallback -> {total_reported}")
            except Exception:
                pass

    # Page size & first page cards
    page_size = None
    cards_found_page1 = 0
    try:
        if hasattr(adapter, 'discover_page_size'):
            page_size = adapter.discover_page_size(driver)  # type: ignore[attr-defined]
    except Exception:
        page_size = None
    try:
        if hasattr(adapter, 'get_job_card_elements'):
            elems = adapter.get_job_card_elements(driver)  # type: ignore[attr-defined]
            cards_found_page1 = len(elems) if elems is not None else 0
        else:
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            cards = adapter.parse_listing_cards(cfg, soup) or []
            cards_found_page1 = len(cards)
    except Exception:
        cards_found_page1 = 0
    if page_size is None or page_size <= 0:
        page_size = max(cards_found_page1, 22)
        logger.debug(f"[INFO] page_size inferred -> {page_size}")

    total_pages = (int((total_reported + page_size - 1) / page_size) if total_reported else (1 if cards_found_page1 else 0))
    logger.debug(f"[PAGINATION] total_listings={total_reported}, page_size={page_size}, total_pages={total_pages}")

    # Dedupe preload
    try:
        seen_ids = set(seen_ids_for_site(cfg.site_id))  # type: ignore
    except Exception:
        seen_ids = set()
    logger.debug(f"[DB] seen_ids preload for site={cfg.site_id}: count={len(seen_ids)}")

    # Upsert helper
    def _upsert_card(card_el) -> bool:
        nonlocal inserted, skipped_existing, scored_not, scored_mid, scored_high
        try:
            if hasattr(card_el, 'get_attribute'):
                card_html = card_el.get_attribute('outerHTML') or ''
                card_soup = BeautifulSoup(card_html, 'html.parser')
            else:
                card_soup = card_el
            data = adapter.extract_listing_minimal(cfg, card_soup) or {}
            listing_id = data.get('listing_id')
            title = data.get('title') or ''
            company = data.get('company') or ''
            location = data.get('location') or ''
            url_ = data.get('url') or ''

            if not listing_id:
                logger.debug(f"[WARN] No listing_id extracted -> SKIP | title={title!r} url={url_!r}")
                return False

            if listing_id in seen_ids:
                skipped_existing += 1
                logger.debug(f"[DUP] listing_id={listing_id} already seen -> SKIP")
                return False

            # --- Compute preliminary score BEFORE upsert (pass listing_id for logging) ---
            prelim_score, excluded, reasons = _apply_card_scoring(
                {
                    'listing_id': listing_id,
                    'title': title,
                    'company': company,
                    'location': location,
                    'url': url_
                },
                criteria_rows,
                base_score=3
            )
            queue_deep = (not excluded) and (prelim_score >= provisional_threshold)

            # --- Upsert minimal row ---
            ok = False
            errA = errB = None
            try:
                ok = bool(upsert_listing_minimal(listing_id, keyword_id, title, company, location, url_, cfg.site_id))  # type: ignore
            except Exception as e1:
                errA = e1
            if not ok:
                try:
                    ok = bool(upsert_listing_minimal(cfg.site_id, keyword_id, listing_id, title, company, location, url_))  # type: ignore
                except Exception as e2:
                    errB = e2

            if not ok:
                ok_sql, msg = _direct_upsert_listing_row(listing_id, keyword_id, title, company, location, url_, cfg.site_id)
                if errA: logger.debug(f"[UPSERT-A] exception: {errA}")
                if errB: logger.debug(f"[UPSERT-B] exception: {errB}")
                logger.debug(f"[UPSERT-SQL] {listing_id} -> {msg}")
                ok = ok_sql

            if ok:
                seen_ids.add(listing_id)
                inserted += 1

                # Persist computed score/status (now listing exists)
                try:
                    _update_listing_score(listing_id, cfg.site_id, int(prelim_score), bool(queue_deep))
                except Exception as e3:
                    logger.debug(f"[ERROR] upsert pipeline exception: {e3}")

                # Tally buckets
                if prelim_score < 2:
                    scored_not += 1
                elif prelim_score == 2:
                    scored_mid += 1
                else:
                    scored_high += 1
                logger.debug(f"[SCORING] {listing_id} score={prelim_score} excluded={excluded} queued_deep={queue_deep} reasons={reasons}")
                return True
            else:
                logger.debug(f"[FAIL] upsert failed listing_id={listing_id} title={title!r} url={url_!r}")
                return False

        except Exception as e:
            logger.debug(f"[ERROR] upsert pipeline exception: {e}")
            return False

    # Process page 1
    try:
        if 'elems' in locals() and elems is not None:
            for el in elems:
                _upsert_card(el)
        else:
            try:
                cards  # type: ignore[name-defined]
            except Exception:
                soup1 = BeautifulSoup(driver.page_source, 'html.parser')
                cards = adapter.parse_listing_cards(cfg, soup1) or []
            for c in cards:
                _upsert_card(c)
    except Exception:
        pass

    if getattr(config, 'SAVE_ARTIFACTS', True):
        save_artifacts(driver, cfg.site_name, keyword, f"p{page:02d}_initial")
    logger.debug(f"[VERIFY] url={cur_url} total_reported={total_reported} cards_found_on_page_{page}={cards_found_page1}")

    # Remaining pages
    if total_pages > 1:
        for p in range(2, total_pages + 1):
            urlp = adapter.build_search_url(cfg, keyword, p)
            logger.debug(f"[NAV] {cfg.site_name} -> {urlp}")
            driver.get(urlp)
            slowmo()
            t0 = time.time()
            while True:
                try:
                    if driver.find_elements(By.CSS_SELECTOR, total_sel):
                        break
                except Exception:
                    pass
                if _any_cards_present():
                    break
                if time.time() - t0 > wait_timeout:
                    break
                time.sleep(0.2)
            time.sleep(settle_delay)

            page_cards: List[Any] = []
            try:
                if hasattr(adapter, 'get_job_card_elements'):
                    page_cards = adapter.get_job_card_elements(driver) or []  # type: ignore[attr-defined]
                else:
                    sp = BeautifulSoup(driver.page_source, 'html.parser')
                    page_cards = adapter.parse_listing_cards(cfg, sp) or []
            except Exception:
                page_cards = []
            logger.debug(f"[PAGE {p}/{total_pages}] cards_found: {len(page_cards)}")
            for el in page_cards:
                _upsert_card(el)
            if getattr(config, 'SAVE_ARTIFACTS', True):
                save_artifacts(driver, cfg.site_name, keyword, f"p{p:02d}")

    # Final summary for this keyword
    LAST_SUMMARY.update({
        'keyword': keyword,
        'site': cfg.site_name,
        'inserted': inserted,
        'skipped_existing': skipped_existing,
        'total_reported': total_reported,
        'scored': {'not': scored_not, 'mid': scored_mid, 'high': scored_high}
    })
    logger.debug(
        f"[SUMMARY] keyword='{keyword}' inserted={inserted} skipped_existing={skipped_existing} total_reported={total_reported} "
        f"scored=(not:{scored_not}, mid:{scored_mid}, high:{scored_high})"
    )

    log_run_banner(False, f"{cfg.site_name} | kw[{kw_index}/{total_keywords}] '{keyword}'")
    return inserted, total_reported

# -----------------------------------------------------------------------------
# Site registry helpers
# -----------------------------------------------------------------------------
def get_default_seek_config() -> SiteConfig:
    return SiteConfig(
        site_id=1,
        site_name="Seek",
        url="https://www.seek.com.au",
        tag_for_result_count='[data-automation="totalJobsCountBcues"]',
        tag_for_cards='article[data-automation="job-card"]',
        tag_for_title='a[data-automation="jobTitle"]',
        tag_for_company='[data-automation="jobCompany"]',
        tag_for_location='[data-automation="jobLocation"]',
    )

def _cfg_from_row(site_id: int, site_name: str, base_url: str, extra_json: Optional[str]) -> SiteConfig:
    cfg = SiteConfig(site_id=site_id, site_name=site_name, url=base_url)
    if extra_json:
        try:
            data = json.loads(extra_json)
            cfg.tag_for_result_count = data.get("tag_for_result_count", cfg.tag_for_result_count)
            cfg.tag_for_cards = data.get("tag_for_cards", cfg.tag_for_cards)
            cfg.tag_for_title = data.get("tag_for_title", cfg.tag_for_title)
            cfg.tag_for_company = data.get("tag_for_company", cfg.tag_for_company)
            cfg.tag_for_location = data.get("tag_for_location", cfg.tag_for_location)
        except Exception:
            pass
    return cfg

def load_sites(only_enabled: bool = True) -> Dict[int, SiteConfig]:
    """
    Load Sites from DB (table 'Sites'). If missing/empty, return a default Seek config as site_id=1.
    """
    sites: Dict[int, SiteConfig] = {}
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        query = "SELECT site_id, site_name, base_url, enabled, extra_json FROM Sites"
        if only_enabled:
            query += " WHERE enabled = 1"
        c.execute(query)
        rows = c.fetchall()
        conn.close()

        if not rows:
            cfg = get_default_seek_config()
            sites[cfg.site_id] = cfg
            return sites

        for site_id, site_name, base_url, enabled, extra_json in rows:
            cfg = _cfg_from_row(site_id, site_name, base_url, extra_json)
            sites[site_id] = cfg

        return sites
    except Exception:
        cfg = get_default_seek_config()
        sites[cfg.site_id] = cfg
        return sites

# -----------------------------------------------------------------------------
# Adapter factory and keyword shim
# -----------------------------------------------------------------------------
_ADAPTERS_BY_KEY: Dict[str, Type[SiteAdapter]] = {
    "seek": SeekAdapter,
    "generic": SiteAdapter,
}

def get_adapter_for(site: Union[SiteConfig, str, int]) -> SiteAdapter:
    """
    Return a concrete adapter instance for a given site.
    """
    key: Optional[str] = None
    if isinstance(site, SiteConfig):
        key = (site.site_name or "").strip().lower()
    elif isinstance(site, str):
        key = site.strip().lower()
    elif isinstance(site, int):
        key = "seek" if site == 1 else "generic"
    else:
        key = "generic"

    cls: Optional[Type[SiteAdapter]] = _ADAPTERS_BY_KEY.get(key)
    if cls is None:
        if key and "seek" in key:
            cls = SeekAdapter
        else:
            cls = SiteAdapter
    return cls()

def active_keywords_for_user(user_id: Optional[int] = None,
                             only_enabled_keywords: bool = True,
                             only_enabled_roles: bool = True) -> List[Tuple[int, str]]:
    """
    Legacy shim. Returns list of (keyword_id, keyword), filtered by enabled flags.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        query = (
            "SELECT k.keyword_id, k.keyword "
            "FROM Keywords k "
            "JOIN Roles r ON r.role_id = k.role_id "
        )
        cond = []
        if only_enabled_keywords:
            cond.append("k.enabled = 1")
        if only_enabled_roles:
            cond.append("r.enabled = 1")
        if cond:
            query += "WHERE " + " AND ".join(cond) + " "
        query += "ORDER BY k.keyword"
        rows = c.execute(query).fetchall()
        conn.close()
        return [(row[0], row[1]) for row in rows]
    except Exception:
        return [(1, "Kitchen Hand"), (2, "Dishwasher")]

__all__ = [
    "SiteConfig",
    "SiteAdapter",
    "SeekAdapter",
    "make_driver",
    "scrape_site_summary",
    "load_sites",
    "get_default_seek_config",
    "get_adapter_for",
    "active_keywords_for_user",
    "configure_logging",
    "log_run_banner",
    "LAST_SUMMARY",
]
