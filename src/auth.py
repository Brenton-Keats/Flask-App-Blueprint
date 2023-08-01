"""Contains methods for handling client authentication.

This contains basic authentication methods as an example that should be
reimplemented.

Author: Brenton Keats, 2023
"""

# Builtins
from functools import wraps

# Libraries
from flask import request, current_app as app
from werkzeug.exceptions import Unauthorized

# Project-level modules
from constants import AUTH_TOKEN_NAME, AUTH_SUPER, AUTH_EDITOR, AUTH_VIEWER

def check_auth_level(required: int, actual: int):
    """Simple authentication method. Smaller numbers are considered superior
    auth levels.

    Args:
        required (int): Minimum authentication level.
        actual (int): Level to check against required.
    """

    if actual is None:
        return False
    return int(actual) <= required


def require_auth(level: int = AUTH_SUPER):
    """Decorate a method to require a specific access level.

    Args:
        level (int, optional): Value to meet or exceed. Passed via
        request.headers, see API authorizations. Defaults to AUTH_SUPER.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            usr_level = request.headers.get(AUTH_TOKEN_NAME, None)
            if check_auth_level(level, usr_level):
                return func(*args, **kwargs)
            else:
                raise Unauthorized("Access level insufficient")
        return wrapper

    return decorator
