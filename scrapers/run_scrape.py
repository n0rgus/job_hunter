from scrapers.site_adapter import load_sites, get_adapter_for, make_driver, scrape_site_summary
from utils.db_helpers import get_active_keywords

def run_scrape_session(user_id: int = 0):
    sites = load_sites(only_enabled=True)
    keywords = get_active_keywords()   # -> [(keyword_id, keyword), ...]

    for _, cfg in sites.items():
        adapter = get_adapter_for(cfg)
        driver = make_driver()
        try:
            for i, (kid, kw) in enumerate(keywords, start=1):
                scrape_site_summary(
                    driver, cfg, adapter,
                    keyword_id=kid, keyword=kw,
                    user_id=user_id, kw_index=i, total_keywords=len(keywords),
                )
        finally:
            driver.quit()
