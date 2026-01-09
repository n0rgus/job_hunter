from flask import Flask
from dashboard.routes.main import main_bp
from api.scrape_api_flask import bp as scrape_bp

app = Flask(__name__)
app.register_blueprint(main_bp)    # page
app.register_blueprint(scrape_bp)  # API
