from tqdm import tqdm
from db_utils import get_active_keywords, insert_run_summary
from scrapers.seek_scraper import scrape_seek

SCRAPERS = [("Seek", scrape_seek)]

def main():
    active_keywords = get_active_keywords()
    total_keywords = len(active_keywords)
    print(f"Loaded {total_keywords} active keywords.\n")

    for site_name, scraper_func in SCRAPERS:
        print(f"=== Scraping {site_name} ===")
        for idx, (keyword_id, keyword) in enumerate(active_keywords):
            listings = scraper_func(keyword_id, keyword, idx, total_keywords)
            listings_found = len(listings)
            highly_suitable = sum(1 for l in listings if l["suitability_score"] >= 3)
            insert_run_summary(keyword_id, listings_found, highly_suitable)

if __name__ == "__main__":
    main()
