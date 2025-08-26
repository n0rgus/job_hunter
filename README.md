# Job Hunter — Setup & Run

## Environment
- Python **3.10+** (recommended 3.11)
- Google Chrome + matching **ChromeDriver** on PATH

## Quickstart (Windows / macOS / Linux)
```bash
# (Recommended) create a virtual environment
python -m venv .venv
# activate: Windows
.venv\Scripts\activate
# activate: macOS/Linux
source .venv/bin/activate

# Install dependencies
pip install flask selenium beautifulsoup4 tqdm pandas

# Run dashboard (http://127.0.0.1:5000)
python -m dashboard.dashboard

# Run scraper
python main_scraper.py
```

## ChromeDriver
- Ensure the **ChromeDriver** version matches your installed Chrome.
- Place the driver binary on your **PATH** (or set `webdriver.Chrome(service=Service(executable_path=...))`).

## Notes
- Database path is absolute by default: see `config.DB_PATH` and `dashboard/utils/db_helpers.py::DB_FILE`.
- Progress JSON: `scrape_progress.json` (consumed by `/progress`).
