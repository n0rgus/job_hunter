# scrapers/debug_tools.py
# Reusable debug helpers for scraping: artifacts, highlighting, waits, slowmo

from __future__ import annotations

import os
import pathlib
import time
from typing import Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Ensure config
import sys
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
import config


def slowmo():
    ms = int(getattr(config, 'SLOWMO_MS', 0) or 0)
    if ms > 0:
        time.sleep(ms / 1000.0)


def ensure_debug_dir() -> pathlib.Path:
    dbg_dir = pathlib.Path('debug_pages')
    if getattr(config, 'DEBUG_SAVE_HTML', True) or getattr(config, 'DEBUG_SAVE_SCREENSHOTS', True):
        dbg_dir.mkdir(exist_ok=True)
    return dbg_dir


def save_artifacts(driver, site_name: str, keyword: str, suffix: str):
    """Save page_source and screenshot based on toggles."""
    dbg_dir = ensure_debug_dir()
    fname = f"{site_name.lower()}_{keyword.replace(' ', '-')}_{suffix}"
    if getattr(config, 'DEBUG_SAVE_HTML', True):
        (dbg_dir / f"{fname}.html").write_text(driver.page_source, encoding='utf-8', errors='ignore')
    if getattr(config, 'DEBUG_SAVE_SCREENSHOTS', True):
        driver.save_screenshot(str(dbg_dir / f"{fname}.png"))


def highlight_cards(driver, css_selector: str) -> Optional[int]:
    js = (
        "(function(){var nodes=document.querySelectorAll(" + repr(css_selector) + ");"
        "nodes.forEach(function(n){n.style.boxShadow='0 0 0 3px rgba(255,0,0,0.6)';n.style.borderRadius='6px';});"
        "return nodes.length;})()"
    )
    try:
        return driver.execute_script(js)
    except Exception:
        return None


def wait_for_results(driver, badge_selector: str, cards_selector: str, timeout: int = 12):
    try:
        WebDriverWait(driver, timeout).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, badge_selector)),
                EC.presence_of_element_located((By.CSS_SELECTOR, cards_selector or 'article')),
            )
        )
    except Exception:
        # Let caller decide how to handle; they may save artifacts
        pass
