# dashboard/dashboard.py
from flask import Flask
from dashboard.routes.main import main_bp
from dashboard.routes.applications import applications_bp
from dashboard.api_admin import api_admin
from dashboard.api_scrape import api_scrape
from api.scrape_api_flask import bp as scrape_bp

app = Flask(__name__)
app.register_blueprint(main_bp)
app.register_blueprint(applications_bp)
app.register_blueprint(api_admin)
app.register_blueprint(api_scrape)
app.register_blueprint(scrape_bp)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
