

from flask import current_app as app
from flask_sqlalchemy import BaseQuery
from sqlalchemy import func, types, or_
from sqlalchemy.sql.elements import Label

from constants import DB_SESSION_TIMEOUT
from .core import db

def generic_search(query: BaseQuery, columns: tuple, pattern: str) -> BaseQuery:
    """Filters the query to require at least one column to include (string-wise) `pattern`.

    Args:
        query (BaseQuery): Query to be filtered
        columns (tuple): Columns of the query to filter on
        pattern (str): Pattern to require in at least one of `columns`. Case insensitive

    Returns:
        BaseQuery: Filtered query
    """
    general_where = []
    for column in columns:

        # Select the object that refers to the DB column
        element = column  # Default to the object (i.e. if unlabeled)
        if isinstance(column, Label):
            element = column.element  # obj.element reference (i.e. if labeled)

        # Skip aggregate function results (cannot use in WHERE clauses)
        if (
            not isinstance(element, func._().__class__)  # Exclude all func.* columns EXCEPT...
            or element.__str__().lower().split("(", maxsplit=1)[0] in ('to_char',)  # Except for those with these names
        ):
            try:
                general_where.append(column.cast(types.String).ilike(f"%{pattern}%"))
            except AttributeError as e:
                app.logger.warn(f'Skipping filter on column {column} with error: {e.__str__()}')
    return query.where(or_(*general_where))
