from flask import Blueprint, render_template, redirect, request, url_for
import sqlite3, json
from utils.db_helpers import get_roles, toggle_role_enabled, add_role, toggle_keyword_enabled, add_keyword

main_bp = Blueprint('main', __name__)

@main_bp.route("/")
def home():
    roles = get_roles()
    return render_template("home.html", roles=roles)

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
