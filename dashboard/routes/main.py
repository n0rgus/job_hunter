from flask import Blueprint, render_template, redirect, request, url_for
import json
from utils.db_helpers import (
    add_keyword,
    add_role,
    get_keywords,
    get_listings,
    get_roles,
    toggle_keyword_enabled,
    toggle_role_enabled,
    update_listing_status,
)

main_bp = Blueprint('main', __name__)

@main_bp.route("/")
def home():
    scanned_since = request.args.get("scanned_since", "")
    hide_disabled = request.args.get("hide_disabled") == "on"
    roles, totals = get_roles(scanned_since or None)
    return render_template(
        "home.html",
        roles=roles,
        totals=totals,
        scanned_since=scanned_since,
        hide_disabled=hide_disabled,
    )
@main_bp.route("/progress")
def progress():
    try:
        with open("scrape_progress.json", "r") as f:
            progress = json.load(f)
    except:
        progress = None
    return render_template("progress.html", progress=progress)

@main_bp.route("/role/toggle/<int:role_id>", methods=["POST"])
def toggle_role(role_id):
    toggle_role_enabled(role_id)
    return redirect(url_for("main.home"))

@main_bp.route("/role/add", methods=["POST"])
def add_role_view():
    role_name = request.form.get("role_name", "")
    if role_name:
        add_role(role_name)
    return redirect(url_for("main.home"))

@main_bp.route("/keyword/toggle/<int:keyword_id>", methods=["POST"])
def toggle_keyword(keyword_id):
    toggle_keyword_enabled(keyword_id)
    return redirect(url_for("main.home"))

@main_bp.route("/keyword/add/<int:role_id>", methods=["POST"])
def add_keyword_view(role_id):
    keyword = request.form.get("keyword", "")
    if keyword:
        add_keyword(keyword, role_id)
    return redirect(url_for("main.home"))

@main_bp.route("/listings")
def listings():
    selected_keyword = request.args.get("keyword", "")
    suitability = request.args.get("suitability", "")
    scanned_since = request.args.get("scanned_since", "")

    keywords = get_keywords()
    listings = get_listings(
        keyword=selected_keyword or None,
        suitability=suitability or None,
        scanned_since=scanned_since or None,
    )

    return render_template(
        "listings.html",
        keywords=keywords,
        listings=listings,
        selected_keyword=selected_keyword,
        suitability=suitability,
        scanned_since=scanned_since,
    )
@main_bp.route("/update_status/<listing_id>", methods=["POST"])
def update_status(listing_id):
    status = request.form.get("status", "new")
    update_listing_status(listing_id, status)

    keyword = request.form.get("keyword", "")
    suitability = request.form.get("suitability", "")
    role_id = request.form.get("role_id", "")
    params = {}
    if keyword:
        params["keyword"] = keyword
    if suitability:
        params["suitability"] = suitability
    if role_id:
        params["role_id"] = role_id
    return redirect(url_for("main.listings", **params))

