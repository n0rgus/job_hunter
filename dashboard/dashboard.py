from flask import Flask
from routes.main import main_bp
from routes.applications import applications_bp

app = Flask(__name__)
app.register_blueprint(main_bp)
app.register_blueprint(applications_bp)

if __name__ == "__main__":
    app.run(debug=True)
