# config.py
# Central configuration for Job Hunter project
from __future__ import annotations
import os

# --- Debug 'Master' Switch ---
INTERACTIVE_DEBUG = False

# --- Paths ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.abspath(os.path.join(BASE_DIR, "job_hunt.db"))
PROGRESS_FILE = os.path.abspath(os.path.join(BASE_DIR, "scrape_progress.json"))

# --- Run context ---
USER_ID = 1                 # default user partition
SITES_INCLUDE = []          # [] = all enabled sites in DB; or e.g. ["Seek"]
KEYWORD_LIMIT_PER_SITE = None  # e.g. 10 for quick tests

# --- Selenium / scraping pacing ---
WAIT_PAGE_SEC = 2           # delay after loading a results page
WAIT_JOB_SEC = 2            # delay when loading a job detail page
MAX_PAGES_PER_KEYWORD = None  # None = let adapter/total govern; or set integer

# --- Deep scan controls ---
ENABLE_DEEP_SCAN = True
DEEP_SCAN_THRESHOLD = 4     # only listings with >= this score get deep scanned
DEEP_SCAN_LIMIT_PER_KEYWORD = 50  # safety cap per keyword

# --- Scoring controls ---
BASE_ENTRY_SCORE = 3        # default score when minimally inserted
MAX_SCORE = 5               # clamp high
MIN_SCORE = 1               # clamp low

# Criteria evaluation:
#   - Each matching positive term adds +1 (capped at MAX_SCORE)
#   - A term prefixed with '!' subtracts 1 (floored at MIN_SCORE)
CRITERIA_POSITIVE_WEIGHT = 1
CRITERIA_NEGATIVE_WEIGHT = -1

if INTERACTIVE_DEBUG:
    # --- Runtime mode ---
    DEBUG_VISUAL = True           # force visible browser
    HEADLESS = False
    VIEWPORT = (1400, 1000)       # window size

    # --- Debug/diagnostic features ---
    SAVE_ARTIFACTS = True         # writing HTML/PNG snapshots per page
    HIGHLIGHT_UI = True           # generate JS outlines around elements
    SLOWMO_MS = 400               # artificial delay between steps

    # --- Navigation/retries ---
    RETRY_ON_BLANK = 2            # retry loop on blank pages if your network is stable
else:
    # --- Runtime mode ---
    DEBUG_VISUAL = False          # don't keep the browser visible
    HEADLESS = False              # run Chrome headless
    VIEWPORT = (1200, 900)        # smaller window renders faster

    # --- Debug/diagnostic features ---
    SAVE_ARTIFACTS = False        # stop writing HTML/PNG snapshots per page
    HIGHLIGHT_UI = False          # skip JS outlines around elements
    SLOWMO_MS = 0                 # no artificial delay between steps

    # --- Navigation/retries ---
    RETRY_ON_BLANK = 0            # disable retry loop on blank pages if your network is stable

# --- Now Unused?
#DEBUG_SAVE_HTML = True    # dump HTML to ./debug_pages/
#DEBUG_SAVE_SCREENSHOTS = True
#PAUSE_ON_ZERO = False     # set True to require Enter when no cards parsed

# --- Waiting/settling (balance speed vs reliability) ---
WAIT_FOR_RESULTS_TIMEOUT = 4  # how long wait_for_results will wait (seconds)
WAIT_FOR_CARDS_TIMEOUT = 3    # optional: if your wait helper supports a separate card timeout
PAGE_SETTLE_DELAY = 0.15      # used after navigation between pages (vs. 0.5)

# --- SEEK defaults (unchanged) ---
DEFAULT_LOCATION_SLUG = "Ringwood-VIC-3134"
DEFAULT_DISTANCE_KM   = 10

# --------------------------------------------------------------------------------------
# Backward-compat aliases & additional constants required by newer modules (non-destructive)
# --------------------------------------------------------------------------------------

# Some modules expect these names:
PROJECT_ROOT = BASE_DIR
DB_FILE = DB_PATH
SCRAPE_PROGRESS_FILE = PROGRESS_FILE

# Optional: artifacts and logs directories used by debug helpers / logger
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(ARTIFACTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Additional browser toggles used by newer adapter code
BLOCK_IMAGES = False
PAGE_LOAD_TIMEOUT = 25  # driver page-load timeout (seconds)

# Scoring diagnostics and queue threshold used by site_adapter
# Listings with score >= PROVISIONAL_THRESHOLD are queued for deep scan
PROVISIONAL_THRESHOLD = BASE_ENTRY_SCORE
LOG_SCORING_MATCHES = True  # very verbose; written to logs/jobhunter.log

# --------------------------------------------------------------------------------------
# Env var overrides (optional)
# --------------------------------------------------------------------------------------
if os.environ.get("JH_SHOW") == "1":
    DEBUG_VISUAL = True
    HEADLESS = False
if os.environ.get("JH_SLOWMO_MS"):
    try:
        SLOWMO_MS = int(os.environ["JH_SLOWMO_MS"])
    except:
        pass
if os.environ.get("JH_PAUSE_ON_ZERO") == "1":
    PAUSE_ON_ZERO = True
if os.environ.get("JH_BLOCK_IMAGES") == "1":
    BLOCK_IMAGES = True
if os.environ.get("JH_LOG_MATCHES") == "1":
    LOG_SCORING_MATCHES = True
