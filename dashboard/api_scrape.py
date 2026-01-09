# dashboard/api_scrape.py
import threading, time, traceback, logging
from flask import Blueprint, jsonify, request

from scrapers.site_adapter import load_sites, get_adapter_for, make_driver, scrape_site_summary

try:
    from utils.db_utils import get_active_keywords
except ImportError:
    # back-compat if you also add a shim later
    from utils.db_helpers import get_active_keywords  # noqa

api_scrape = Blueprint("api_scrape", __name__, url_prefix="/api/scrape")
log = logging.getLogger("job_hunter.api_scrape")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

SCRAPE_LOCK = threading.Lock()
SCRAPE_RUNNING = False
LAST_ERROR: str | None = None

def _run_scrape_bg(user_id: int = 0):
    global SCRAPE_RUNNING, LAST_ERROR
    try:
        log.info("[SCRAPE] START (user_id=%s)", user_id)
        print("[SCRAPE] START")
        sites = load_sites(only_enabled=True)
        keywords = get_active_keywords()
        for _, cfg in sites.items():
            adapter = get_adapter_for(cfg)
            driver = make_driver()
            try:
                for i, (kid, kw) in enumerate(keywords, start=1):
                    # Page 1 URL will be printed from scrape_site_summary (see step 1)
                    scrape_site_summary(
                        driver, cfg, adapter,
                        keyword_id=kid, keyword=kw,
                        user_id=user_id, kw_index=i, total_keywords=len(keywords),
                    )
            finally:
                driver.quit()
        log.info("[SCRAPE] FINISHED")
        print("[SCRAPE] FINISHED")
    except Exception as e:
        LAST_ERROR = f"{e}\n{traceback.format_exc()}"
        log.exception("[SCRAPE] FAILED: %s", e)
        print("[SCRAPE] FAILED:", e)
    finally:
        with SCRAPE_LOCK:
            globals()["SCRAPE_RUNNING"] = False

@api_scrape.post("/start")
def start_scrape():
    global SCRAPE_RUNNING, LAST_ERROR
    user_id = int(request.args.get("user_id", "0"))
    with SCRAPE_LOCK:
        if SCRAPE_RUNNING:
            return jsonify({"ok": False, "reason": "already_running"}), 409
        SCRAPE_RUNNING = True
        LAST_ERROR = None
        t = threading.Thread(target=_run_scrape_bg, args=(user_id,), daemon=True)
        t.start()
    # 202 Accepted: work has started asynchronously
    return jsonify({"ok": True, "status": "started"}), 202

@api_scrape.get("/status")
def scrape_status():
    return jsonify({"running": SCRAPE_RUNNING, "last_error": LAST_ERROR})
