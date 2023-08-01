"""Define constant values to be used across the application.

Author: Brenton Keats, 2023
"""

MAX_PAGE_SIZE = 1000     # API pagination size limit (records per page)
DB_SESSION_TIMEOUT = 30  # Minutes before a session is marked as inactive
AUTH_TOKEN_NAME = 'X-API-KEY'

# Authentication levels. Lower numbers equate to higher access levels.
AUTH_SUPER = 0
AUTH_EDITOR = 1
AUTH_VIEWER = 2
