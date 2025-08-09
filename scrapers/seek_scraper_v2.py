import json, re, sqlite3, time
from datetime import date
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from db_utils import insert_job_listing

DB_FILE = "job_hunt.db"
PROGRESS_FILE = "scrape_progress.json"

# ----- Selenium setup -----
options = Options()
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option('excludeSwitches', ['enable-logging'])
service = Service(log_path='NUL')
driver = webdriver.Chrome(options=options, service=service)

# ----- Suitability & Extraction Config -----
license_keywords = ["driver's license", "drivers licence", "own transport", "own car", "vehicle required", "forklift"]
no_exp_keywords = ["no experience", "no prior experience", "training provided", "on the job training"]

prefix = "https://www.seek.com.au/"
suffix = "-jobs/in-Ringwood-VIC-3134?distance=10"
delay_between_pages = 3
delay_between_jobs = 4

def save_progress(keyword, keyword_idx, total_keywords,
                  processed_count, total_listings,
                  count_not, count_suitable, count_high,
                  deep_scanned=0, total_deep=0, skipped_count=0,
                  phase="PASS 1"):
    progress_data = {
        "phase": phase,
        "keyword": keyword,
        "keyword_index": keyword_idx + 1,
        "total_keywords": total_keywords,
        "processed_count": processed_count,
        "total_listings": total_listings,
        "not_suitable": count_not,
        "suitable": count_suitable,
        "highly_suitable": count_high,
        "deep_scanned": deep_scanned,
        "total_deep": total_deep,
        "skipped_existing": skipped_count
    }
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress_data, f, indent=2)

def extract_job_id(url):
    match = re.search(r"/job/(\d{8})", url)
    return match.group(1) if match else None

def classify_experience(title):
    title_lower = title.lower()
    if any(k in title_lower for k in ["manager", "supervisor", "senior", "lead"]):
        return "Senior"
    if any(k in title_lower for k in ["trainee", "junior", "crew", "team member", "assistant", "hand"]):
        return "Entry"
    return "Mid"

def score_suitability(exp_level, requires_license, no_experience):
    if exp_level == "Senior" or requires_license:
        return 1
    if exp_level == "Entry" and no_experience:
        return 5
    return 3

def extract_pay(desc):
    patterns = [
        re.compile(r"\$\d{2,3}(?:,\d{3})?(?:\.\d{2})?\s*(?:per hour|/hr|hourly|p/h)", re.I),
        re.compile(r"\$\d{2,3}(?:,\d{3})?(?:\.\d{2})?\s*(?:per week|weekly)", re.I),
        re.compile(r"\$\d{2,3}(?:,\d{3})?(?:\.\d{2})?\s*(?:per year|annual|annum|p\.a\.)", re.I),
    ]
    for pat in patterns:
        match = pat.search(desc)
        if match:
            return match.group(0)
    return ""

def extract_closing_date(desc):
    match = re.search(r"(close[s]?|apply by)\s+(?:on\s+)?([0-9]{1,2}\s+\w+\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})", desc, re.I)
    return match.group(2) if match else ""

def get_total_listings(soup):
    count_div = soup.find("div", attrs={"data-automation": re.compile(r"^totalJobsCount", re.I)})
    if count_div:
        text = count_div.get_text(strip=True)
        match = re.search(r"(\d[\d,]*)", text)
        if match:
            return int(match.group(1).replace(",", ""))
    return len(soup.find_all("article"))

def load_seen_ids():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT listing_id FROM Job_Listings")
    seen = {row[0] for row in c.fetchall()}
    conn.close()
    return seen

def scrape_seek(keyword_id, keyword, keyword_index=0, total_keywords=1):
    listings = []
    seen_ids = load_seen_ids()
    skipped_count = 0
    processed_count = 0

    slug = keyword.replace(" ", "-")
    page = 1

    # --- PASS 1 ---
    url = f"{prefix}{slug}{suffix}"
    driver.get(url)
    time.sleep(delay_between_pages)
    soup = BeautifulSoup(driver.page_source, "html.parser")

    total_listings = get_total_listings(soup)
    count_not = count_suitable = count_high = 0

    while True:
        job_cards = soup.find_all("article")
        if not job_cards:
            break

        for card in job_cards:
            title_tag = card.find("a", {"data-automation": "jobTitle"})
            if not title_tag:
                continue

            job_url = "https://www.seek.com.au" + title_tag.get("href")
            job_id = extract_job_id(job_url)
            if not job_id:
                continue

            if job_id in seen_ids:
                skipped_count += 1
                continue
            seen_ids.add(job_id)
            processed_count += 1

            company_tag = card.find("a", {"data-automation": "jobCompany"})

            # Updated location capture
            loc_tag = card.find("span", {"data-automation": "job-detail-location"})
            if loc_tag:
                loc_a = loc_tag.find("a")
                location_text = loc_a.get_text(strip=True) if loc_a else loc_tag.get_text(strip=True)
            else:
                loc_fallback = card.find("a", {"data-automation": "jobLocation"})
                location_text = loc_fallback.get_text(strip=True) if loc_fallback else ""

            title = title_tag.text.strip()
            exp_level = classify_experience(title)
            score = 3 if exp_level != "Senior" else 1

            if score <= 1:
                count_not += 1
            elif score <= 3:
                count_suitable += 1
            else:
                count_high += 1

            job = {
                "listing_id": job_id,
                "keyword_id": keyword_id,
                "title": title,
                "company": company_tag.text.strip() if company_tag else "",
                "location": location_text,
                "url": job_url,
                "listing_date": str(date.today()),
                "suitability_score": score
            }

            insert_job_listing(**job)
            listings.append(job)

        save_progress(keyword, keyword_index, total_keywords,
                      processed_count, total_listings,
                      count_not, count_suitable, count_high,
                      skipped_count=skipped_count, phase="PASS 1")

        if processed_count >= total_listings:
            break

        page += 1
        url = f"{prefix}{slug}{suffix}&page={page}"
        driver.get(url)
        time.sleep(delay_between_pages)
        soup = BeautifulSoup(driver.page_source, "html.parser")

    # --- PASS 2: Deep Scan highly suitable ---
    deep_jobs = [job for job in listings if job["suitability_score"] >= 4]
    total_deep = len(deep_jobs)
    deep_scanned = 0

    for job in deep_jobs:
        deep_scanned += 1

        save_progress(keyword, keyword_index, total_keywords,
                      processed_count, total_listings,
                      count_not, count_suitable, count_high,
                      deep_scanned, total_deep,
                      skipped_count=skipped_count,
                      phase="PASS 2")

        driver.get(job["url"])
        time.sleep(delay_between_jobs)
        soup = BeautifulSoup(driver.page_source, "html.parser")

        desc_tag = soup.find("div", {"data-automation": "jobAdDetails"})
        desc_text = desc_tag.get_text(separator=" ", strip=True) if desc_tag else ""

        requires_license = any(k in desc_text.lower() for k in license_keywords)
        no_experience = any(k in desc_text.lower() for k in no_exp_keywords)
        score = score_suitability(classify_experience(job["title"]), requires_license, no_experience)

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""
            UPDATE Job_Listings
            SET description=?, pay_rate=?, closing_date=?, suitability_score=?, no_license=?, no_experience=?
            WHERE listing_id=?
        """, (
            desc_text[:1000],
            extract_pay(desc_text),
            extract_closing_date(desc_text),
            score,
            int(not requires_license),
            int(no_experience),
            job["listing_id"]
        ))
        conn.commit()
        conn.close()

    return listings, total_listings, skipped_count
