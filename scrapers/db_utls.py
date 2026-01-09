# at the start of scrape_site_summary(...)
seen_ids_session = set()
inserted = updated = skipped_session_dup = skipped_db_dup = failed = 0

# preload DB ids for this site (optional, you log it already)
cur = con.cursor()
existing_ids = set(r[0] for r in cur.execute(
    f"SELECT listing_id FROM {qident(T.data_job_listings)} WHERE site_id=?", (cfg.site_id,)
))
logger.debug("[DB] seen_ids preload for site=%s: count=%d", cfg.site_id, len(existing_ids))

# per card:
lid = listing_id  # from extraction

# (a) in-session duplicate?
if lid in seen_ids_session:
    skipped_session_dup += 1
    logger.debug("[DEDUP] in-session duplicate listing_id=%s -> skip", lid)
    continue
seen_ids_session.add(lid)

# (b) already in DB?
if lid in existing_ids:
    logger.debug("[DEDUP] already in DB listing_id=%s (site=%s)", lid, cfg.site_id)
    # You can choose to UPDATE anyway, or truly skip.
    # Here we still UPSERT so title/url/score refresh:
    action = "updated"
else:
    action = "inserted"

# UPSERT (insert or update)
try:
    payload = {
        "listing_id": lid,
        "site_id": cfg.site_id,
        "keyword_id": keyword_id,
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "suitability_score": score,
        "captured_at": captured_at,   # e.g., datetime.utcnow().isoformat()
        # add any other known columns that exist in your table
    }
    res = upsert_job_listing(con, payload)
    if res == "inserted":
        inserted += 1
    else:
        updated += 1
    # if you decided to *truly* skip DB duplicates above, increment skipped_db_dup instead
except Exception as e:
    failed += 1
    logger.debug("[FAIL] upsert failed listing_id=%s title=%r url=%r", lid, title, url)
    # upsert_job_listing already logged the rendered SQL; nothing else to add here
