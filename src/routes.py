from flask import Blueprint, url_for, redirect, render_template

root_bp = Blueprint('root', __name__, url_prefix='')

@root_bp.route('/', endpoint='index')
def index():
    return render_template('index.jinja2')
