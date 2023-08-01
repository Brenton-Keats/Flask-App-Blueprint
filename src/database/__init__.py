"""Database module root, serves module content and provides setup methods.

Author: Brenton Keats, 2023
"""

# Builtins
import os as __os

# Libraries
from flask import Flask
from sqlalchemy import URL as __URL

# Project-level modules
from .core import db, session_manager
from . import core, models

def get_connection_url():
    return __URL.create(**{
        'drivername': 'postgresql',
        'database': __os.environ.get('POSTGRES_DB'),
        'username': __os.environ.get('POSTGRES_USER'),
        'password': __os.environ.get('POSTGRES_PASSWORD'),
        'host': __os.environ.get('DB_HOST'),
    })


def setup_db(app: Flask):
    if not app.config.get('SQLALCHEMY_DATABASE_URI', None):
        app.config['SQLALCHEMY_DATABASE_URI'] = get_connection_url()
    db.init_app(app)

    with app.app_context():
        db.create_all()
