"""Configure a basic Flask application with a Flask-RestX API and SQLAlchemy backend.

Author: Brenton Keats, 2023
"""

# Libraries
from flask import Flask

# Project-level modules
from api import autoconfigure as get_api
from database import setup_db
from routes import root_bp as routes_bp


def create_app() -> Flask:
    """Configures the Flask application for use.

    Returns:
        Flask: Application ready to be used.
    """

    app = Flask(__name__, template_folder="frontend/templates", static_folder="frontend/static")

    # Configure the DB
    setup_db(app)

    # Generate the API, and register it.
    app.register_blueprint(get_api())

    # Register the frontend routes
    app.register_blueprint(routes_bp)

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
